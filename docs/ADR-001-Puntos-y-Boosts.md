# ADR-001: Persistencia, billetera de puntos y boosts de servidor

- **Estado:** Aceptado. Implementado: esquema y migraciones (§2.1 a §2.4), comprar puntos y su saga (§2.5,
  §2.6, §2.11), boostear y retirar (§2.7, §2.9), ver nivel y beneficios (§2.7), publicador del outbox
  (§2.8). Pendiente: aplicar los beneficios en los otros servicios (§5)
- **Fecha:** 2026-11-24 · **Revisado:** 2026-11-25 (saga de compra, §2.11)
- **Alcance:** `payments-monetization-service`; para aplicar los beneficios, `server-roles-core-service`,
  `chat-engine-service` y los clientes web y mobile. Infraestructura: Security Group de infra (Terraform)

---

## 1. Contexto

Payments era un esqueleto (health checks y CI). Las historias de la épica de Monetización que se implementan
primero son **Comprar puntos**, **Usar puntos para boostear un servidor** y **Ver beneficios de servidor
boosteado** (`docs/CONSIGNA.md` del orquestador). Plan Premium (suscribirse y cancelar) viene después, pero
el esquema ya lo contempla.

Lo que piden:

- Comprar un paquete de puntos con dinero real. Si el pago se aprueba, los puntos se acreditan de inmediato.
  Si se rechaza, no se acredita nada y se informa el error. El usuario ve su saldo y su historial.
- Gastar puntos en boosts. Cada boost es una unidad independiente, permanente y con su propio costo. Un miembro
  puede dar varios al mismo servidor y retirar los suyos sin reembolso. El costo lo define el grupo y se
  documenta.
- Los niveles se alcanzan con 5, 15 y 30 boosts, y se pierden si el servidor queda por debajo del umbral.
  Cada nivel desbloquea beneficios para todos los miembros:
  - Nivel 1: 20 slots de emojis más e ícono animado.
  - Nivel 2: banner y adjuntos de 25 MB.
  - Nivel 3: URL de invitación propia e insignia.
- Ver el nivel, sus beneficios y cuántos boosts faltan para el siguiente.

Restricciones del sistema: cada servicio tiene su base en el RDS compartido (Postgres 18), los eventos entre
servicios van por RabbitMQ con el patrón Outbox (`ARQUITECTURA.md`), y Payments corre en el nodo **infra**,
que hoy no puede hablar con Server-Roles (nodo edge).

## 2. Decisión

### 2.1 Persistencia: SQLAlchemy async + Alembic

- **SQLAlchemy 2 async con asyncpg**, como Auth-User y Server-Roles. La configuración sigue siendo
  `DATABASE_URL`, pero el servicio y Alembic le ponen siempre el driver `postgresql+asyncpg`. Así el compose de
  despliegue, que todavía dice `postgresql+psycopg2://`, funciona con la imagen vieja y con la nueva.
- **Alembic async** (`alembic.ini`, `alembic/env.py`). La imagen corre `alembic upgrade head` antes de
  arrancar: si una migración falla, el contenedor no queda sano y el deploy vuelve solo al tag anterior.
- **Migraciones escritas a mano** cuando tienen `CHECK` o índices parciales (autogenerate no los ve), con los
  nombres de la convención de `app/db/base.py`. El CI corre `alembic check`, que falla si un modelo cambió sin
  su migración.

### 2.2 Billetera y movimientos (libro contable)

- **`wallets`**: el saldo por usuario, con `CHECK (balance >= 0)`. El usuario es el id canónico que pone Kong
  en `X-User-Id`. No hay claves foráneas a otros servicios.
- **`point_transactions`**: un registro que solo se agrega, con un movimiento por fila y `amount` con signo:
  - `purchase`: crédito. Tiene que traer paquete, precio y comprobante del pago, y ese comprobante es único,
    así un mismo cobro no acredita dos veces.
  - `boost`: débito. Apunta al boost que pagó.
  - `refund` y `adjustment`: quedan para devoluciones y correcciones.

  Cada fila guarda `balance_after`, así el historial se muestra sin recalcularlo. **El saldo es siempre la
  suma del historial:** los dos se escriben en la misma transacción.
- **Una compra rechazada no mueve puntos**, así que no queda en `point_transactions`. Queda registrada en
  `point_purchases` como `failed`, con el motivo (§2.11), y en un evento `payment.failed` (§2.8).

### 2.3 Concurrencia: `SELECT ... FOR UPDATE`

- Toda operación que mueve puntos **bloquea primero la fila de la billetera** (`SELECT ... FOR UPDATE`) y,
  en la misma transacción, actualiza el saldo y escribe el movimiento y el evento del outbox. Dos compras o
  boosts simultáneos del mismo usuario se ordenan, y el `CHECK` es la última red si algo falla.
- **`server_boost_counters`**: una fila por servidor con los boosts vigentes y el nivel. Es la fila que se
  bloquea al boostear o retirar. Así dos boosts simultáneos del mismo servidor se ordenan, y el que cruza un
  umbral se detecta **una sola vez**: un solo evento `server.boost_level_changed`, no dos. Además evita contar
  `server_boosts` en cada consulta del nivel.
- **Orden de bloqueo fijo:** primero la billetera, después el contador del servidor, para que dos operaciones
  no se esperen entre sí (deadlock).
- **Aislamiento `READ COMMITTED`**, el default de Postgres. Con los bloqueos explícitos alcanza y no hay que
  reintentar por errores de serialización, como pasaría con `SERIALIZABLE`.

### 2.4 Idempotencia

Las operaciones que cobran o gastan (comprar, boostear) exigen el header **`Idempotency-Key`**, cuyo valor
genera el cliente para cada intento (si falta, `400`). La clave es **por usuario**. Para comprar, la clave
vive en la propia compra (`point_purchases`, único por usuario y clave, §2.11): la fila que guarda el estado de
la saga ya dice qué responder. Para lo demás (boostear), la tabla `idempotency_records` la guarda junto con un
hash del método, la ruta y el cuerpo:

- **Misma clave y mismo pedido:** se devuelve la respuesta guardada (o, en una compra, la que corresponde a su
  estado), sin cobrar de nuevo.
- **Misma clave con otro cuerpo:** `422`. Es un error del cliente.
- **Misma clave mientras el primero sigue en curso:** en una compra, `409`. En un boost, la clave se reclama
  con un `INSERT` en la misma transacción que el boost: el segundo pedido espera ahí a que el primero termine y
  recibe su respuesta.
- **Solo se guardan las respuestas exitosas** de un boost: si falló (saldo insuficiente, no es miembro), la
  transacción se deshace con la clave incluida, y el reintento, por ejemplo después de comprar puntos, se
  ejecuta de verdad.
- **Vencimiento:** los registros duran 24 h (`expires_at`).

### 2.5 Pasarela de pago simulada

No hay pasarela real. El servicio cobra a través de una interfaz (`PaymentGateway.charge(...) →
ChargeResult`), con una sola implementación, **simulada**, que decide según la tarjeta de prueba, con números
como los del modo test de Stripe:

| Tarjeta de prueba | Resultado |
|---|---|
| `4242 4242 4242 4242` | Aprobada |
| `4000 0000 0000 0002` | Rechazada (`card_declined`) |
| `4000 0000 0000 9995` | Rechazada (`insufficient_funds`) |

El cobro aprobado devuelve un comprobante (`sim_<uuid>`), que es el `payment_reference` del movimiento. La
interfaz tiene tres operaciones: `charge` (con una clave de idempotencia: la pasarela no cobra dos veces con
la misma), `refund` (la compensación de §2.11) y `find_charge` (qué pasó con el cobro de una clave, para
conciliar). La simulada guarda sus cobros en memoria, como si fueran los de la pasarela: si el servicio se
reinicia, los olvida, y la conciliación da por no cobradas las compras que hubieran quedado a medias.
Cambiar a Mercado Pago o Stripe es otra implementación de la misma interfaz. Si esa pasarela confirma el pago
de forma asíncrona (webhook), la compra pasa a tener un estado pendiente: eso se decide en otro ADR.

### 2.6 Catálogo de puntos y costo del boost

- **Paquetes**, fijos en código y con los mismos precios que ya muestra la web (Ajustes → Factura):

  | Paquete | Puntos | Precio |
  |---|---|---|
  | `points_500` | 500 | USD 4.99 |
  | `points_1000` | 1.000 | USD 9.49 |
  | `points_2500` | 2.500 | USD 21.99 |
  | `points_5000` | 5.000 | USD 39.99 |

  El cliente manda el **id del paquete**, nunca el precio ni los puntos.
- **Un boost cuesta 500 puntos.** Con el paquete más chico alcanza para uno. Llegar al nivel 3 (30 boosts)
  cuesta 15.000 puntos entre todos los miembros. El costo se guarda en cada boost (`points_spent`): si
  después cambia, los boosts anteriores conservan lo que costaron.

### 2.7 Boosts y niveles

- Cada boost es una fila de `server_boosts`. Son permanentes y no vencen.
- **Retirar un boost** solo lo puede hacer quien lo dio: le pone `removed_at`, sin reembolso. La fila queda
  como historial y el movimiento que lo pagó la sigue apuntando.
- **Niveles:** 0 con menos de 5 boosts vigentes, 1 desde 5, 2 desde 15 y 3 desde 30. El nivel se recalcula en
  la misma transacción que boostea o retira: sube cuando se cruza un umbral y baja cuando se cae debajo.
- **Solo boostean los miembros.** Payments le pregunta a Server-Roles por HTTP (§2.9). Retirar no exige ser
  miembro: quien dejó el servidor puede retirar los boosts que dio.
- **Todo pasa en una transacción local**, sin saga (no hay nada externo que compensar): reclamar la
  `Idempotency-Key`, verificar la membresía, bloquear la billetera, verificar el saldo, bloquear el contador,
  crear el boost, descontar los puntos, recalcular el nivel y dejar los eventos. La consulta a Server-Roles
  (hasta 2 s) se hace con la clave reclamada pero antes de bloquear la billetera.
- **API.** `POST /api/v1/boosts/servers/{server_id}` (con `Idempotency-Key`; `201` con el boost, el estado del
  servidor y el saldo), `DELETE /api/v1/boosts/{boost_id}` (retirar; `404` si no es propio, `409` si ya estaba
  retirado), `GET /api/v1/boosts/mine?server_id=` (mis boosts vigentes, para elegir cuál retirar) y
  `GET /api/v1/boosts/servers/{server_id}`: nivel, boosts vigentes, beneficios desbloqueados, los tres niveles
  con su umbral y sus beneficios, el próximo nivel y cuántos boosts faltan (`null` y `0` en el máximo), y
  cuántos boosts propios tiene quien consulta. Solo para miembros; un servidor sin boosts está en nivel 0. Cada
  beneficio tiene un `id` estable en inglés (`emoji_slots_20`, `animated_icon`, `server_banner`,
  `attachments_25mb`, `vanity_url`, `boosted_badge`), que es el que van a usar los servicios que lo aplican
  (§2.10). Saldo
  insuficiente es `409 insufficient_points` con `balance`, `cost` y `missing` (el saldo faltante del CA 2).
  Otros códigos: `not_a_member` (`403`), `server_not_found` (`404`), `membership_unavailable` (`503`).

### 2.8 Eventos (Outbox)

Exchange `payments.events` (topic, durable). Igual que en Auth-User: el evento se guarda en `outbox_events` en
la misma transacción que el cambio, y un publicador (`app/workers/outbox_publisher.py`) lo manda a RabbitMQ:

- **Al menos una vez.** Se marca como publicado recién cuando RabbitMQ lo confirma (*publisher confirms*). Si
  el proceso se corta en el medio, se vuelve a mandar: el consumidor deduplica por `message_id`, que es el id
  del evento. Los mensajes son persistentes y la routing key es el tipo de evento.
- **En orden.** Va en tandas, por `created_at`. Si una publicación falla, la tanda se corta ahí: lo anterior
  queda marcado y el resto sale en la próxima vuelta, sin que un evento posterior se adelante.
- **`FOR UPDATE SKIP LOCKED`.** Si hay más de un publicador, cada evento lo toma uno solo.
- **Contenedor propio**, `payments-outbox-worker`, con la misma imagen que la API
  (`python -m app.workers.outbox_publisher`), como `auth-outbox-worker` y `server-roles-outbox-worker`. Así la
  API y la publicación se caen y se reinician por separado. Busca pendientes cada 2 s, o sin esperar mientras
  haya tandas llenas.
- **Salud:** toca `/tmp/outbox-heartbeat` después de cada vuelta sana, lo que incluye estar conectado a
  RabbitMQ, y el healthcheck del contenedor falla si tiene más de 30 s. Con SIGTERM (`docker stop`, un deploy)
  termina la tanda en curso antes de salir. Sin `DATABASE_URL` o sin `RABBITMQ_URL` no arranca: sale con
  error, en vez de quedar "sano" sin publicar.
- **Memoria.** Se evaluó correrlo dentro de la API para no sumar un contenedor: los **límites** de memoria del
  nodo infra ya sumaban 1024 MB en 1 GB. Pero un límite es un tope, no lo que se usa. El 2026-11-26 el nodo
  tenía 264 MB disponibles (`free -m`: 913 MB totales, 502 MB usados), los seis contenedores usaban ~306 MB en
  total (`docker stats`) y el worker real usa unos 50 a 70 MB, así que entra, con tope de 96 MB. Queda como riesgo en la deuda técnica I2: si el consumo real se acerca al
  giga, el primer candidato a achicar es Prometheus.
- **En el deploy**, `deploy-service.sh payments-service` recrea la API y el worker juntos, como en Auth.

| Routing key | Cuándo | Lo consume |
|---|---|---|
| `payment.completed` / `payment.failed` / `payment.refunded` | Cobro aprobado, rechazado o devuelto (§2.11) | (auditoría, métricas) |
| `points.purchased` | Se acreditaron puntos | (métricas) |
| `server.boosted` / `server.boost_removed` | Se dio o retiró un boost | (métricas) |
| `server.boost_level_changed` | El servidor cambió de nivel, en cualquier dirección | Server-Roles, Chat |
| `premium.subscribed` y demás de Premium | Historias de Premium | — |

Los nombres de la tarea (`PaymentCompleted`, `ServerBoosted`, `PremiumSubscribed`) son estos mismos eventos,
escritos con el formato `<entidad>.<hecho>` que ya usan los demás servicios.

### 2.9 Membresía: HTTP a Server-Roles

- Para boostear, Payments llama a Server-Roles **`GET /internal/servers/{id}/member-ids`**, como ya hacen Chat
  y Notification (timeout de 2 s), y verifica que el usuario esté en la lista. Si no es miembro, responde
  `403`. Si Server-Roles no responde, `503`: **nunca se boostea sin verificar**.
- Ver el nivel y los beneficios también exige ser miembro.
- **Hace falta una regla de red nueva:** el Security Group de edge tiene que aceptar el puerto 8002 desde el
  grupo de infra. Hoy solo lo acepta desde realtime. Se agrega en Terraform.

### 2.10 Beneficios: los aplica cada servicio dueño

Payments decide el nivel; **cada servicio aplica el beneficio de lo que es suyo**, a partir de
`server.boost_level_changed`. Server-Roles guarda el nivel en el servidor y lo devuelve en sus respuestas, así
los clientes lo tienen sin llamar a Payments.

| Nivel | Beneficio | Dónde se aplica |
|---|---|---|
| 1 | +20 slots de emojis personalizados | Server-Roles, cuando existan los emojis del servidor |
| 1 | Ícono de servidor animado | Server-Roles: aceptar GIF y WebP animado en el ícono |
| 2 | Banner de servidor | Server-Roles: `PUT /servers/{id}/banner` exige nivel 2 |
| 2 | Adjuntos de hasta 25 MB para todos | Chat-Engine. Con Premium personal (50 MB) se usa el mayor de los dos |
| 3 | URL de invitación propia (vanity URL) | Server-Roles |
| 3 | Insignia "Servidor Boosteado" | Clientes, a partir del nivel que devuelve Server-Roles |

**Si el servidor baja de nivel**, lo que ya está no se borra (el banner subido, los emojis de más), pero deja
de poder cambiarse o agregarse hasta volver al nivel. La vanity URL sí se libera, para que no quede tomada por
un servidor que ya no tiene el beneficio.

### 2.11 Saga de compra

**Por qué hace falta.** Cobrar es un sistema externo que no entra en la transacción de la base. Si el cobro se
aprueba y después falla la escritura (se cae la conexión, se reinicia el contenedor), el usuario pagó y no
recibió los puntos. El resto de Payments no necesita saga: boostear y retirar son una sola transacción local, y
los beneficios se aplican por eventos que ningún servicio puede rechazar (§2.10).

**Cómo.** Una saga chica, dentro de Payments, con su estado en `point_purchases` (una fila por compra):

1. **Registrar** la compra en `pending` y confirmar. El paquete, los puntos y el precio se copian del catálogo.
2. **Cobrar** en la pasarela, con el id de la compra como clave de idempotencia.
   - Rechazado: la compra queda `failed` con el motivo, evento `payment.failed`, respuesta `402`.
   - La pasarela no responde: no se sabe si cobró. La compra queda `pending` y se responde `503`
     (`payment_pending`).
3. **Acreditar**, en una sola transacción: bloquear la compra y la billetera (en ese orden), sumar los puntos,
   anotar el movimiento, marcar la compra `completed` y dejar `payment.completed` y `points.purchased` en el
   outbox. Respuesta `201`.
4. **Compensar**, si el paso 3 falla: devolver el cobro (`refund`), marcar la compra `refunded`, evento
   `payment.refunded`, respuesta `503` (`purchase_refunded`). Si la devolución también falla, la compra queda
   `pending`.

**Conciliación.** Cada minuto, el servicio busca las compras `pending` de hace más de 5 minutos (ningún cobro
tarda tanto) y le pregunta a la pasarela por el cobro de cada una (`find_charge`):

| La pasarela dice | La compra queda |
|---|---|
| Aprobado | `completed`: se acredita (recuperación hacia adelante) |
| Rechazado, o no hay cobro | `failed` (`not_charged` si nunca se cobró) |
| Devuelto | `refunded` |

Corre dentro del proceso de la API (tarea de fondo en el `lifespan`), no en un worker aparte, porque la
pasarela simulada guarda sus cobros en memoria y solo los ve ese proceso. Con una pasarela real puede pasar a
un worker como el del outbox. Acreditar es idempotente: el paso 3 no hace nada si la compra ya no está
`pending`, así que la conciliación y un pedido lento no pueden acreditar dos veces.

**API.** `POST /api/v1/points/purchases` (`{package_id, card_number}` y `Idempotency-Key`),
`GET /api/v1/points/wallet` (saldo e historial, paginado con `?limit=&before=`) y `GET /api/v1/points/packages`.
Los errores son `application/problem+json` con un `code` estable (`payment_declined`, `payment_pending`,
`purchase_refunded`, `purchase_in_progress`, `unknown_package`, `idempotency_key_reused`,
`idempotency_key_required`, `unauthenticated`).

## 3. Alternativas descartadas

- **Pasarela real ya (Mercado Pago o Stripe sandbox):** requiere cuentas y credenciales, y en Mercado Pago el
  pago se confirma por webhook, lo que suma un estado pendiente a todas las compras. La interfaz de §2.5 deja
  enchufarla después sin cambiar el resto.
- **Saldo calculado sumando el historial en cada consulta**, sin `wallets`: no hay una fila que bloquear, y
  validar que el saldo alcance obligaría a `SERIALIZABLE` o a bloqueos consultivos (*advisory locks*).
- **Bloqueo optimista** (columna `version` y reintento): con poca concurrencia por usuario funciona igual,
  pero obliga a reintentar en el código. `FOR UPDATE` es más simple y la espera es corta.
- **Advisory locks por servidor** en vez de `server_boost_counters`: no dejan una fila para leer el nivel sin
  contar los boosts.
- **Membresía por eventos** (consumir `member.*` y guardar una copia local): no necesita la regla de red, pero
  duplica datos de Server-Roles, y la copia puede quedar atrasada justo cuando alguien entra y boostea.
- **Beneficios solo en Payments**, consultado por cada servicio en cada operación: cada subida de banner o
  adjunto dependería de que Payments responda.
- **Cobrar y acreditar sin registrar antes la compra:** si el proceso se cae entre el cobro y la acreditación,
  no queda rastro de que se cobró, y no hay forma de devolver el pago o terminar la compra.
- **Saga en todas las operaciones** (boostear, beneficios): agrega estados intermedios y compensaciones a
  operaciones que una transacción local ya resuelve (§2.11).
- **SQLite en los tests**, como Auth-User: no tiene `FOR UPDATE` ni índices parciales, y los `CHECK` se
  comportan distinto. Los tests de Payments corren contra PostgreSQL (§4).

## 4. Consecuencias

- **Tests contra PostgreSQL de verdad**, tanto en el CI como en local, con Docker
  (`docker compose --profile test run --rm tests`). La base de tests es aparte de la de desarrollo, porque los
  tests de migraciones la vacían.
- **Payments depende de Server-Roles para boostear:** si Server-Roles está caído, boostear da `503`. Comprar
  puntos no depende de nadie.
- **Hay que cambiar infraestructura:** la regla infra → edge:8002, `SERVER_ROLES_URL` para la API, el
  contenedor `payments-outbox-worker` (con `RABBITMQ_URL`) en el compose de despliegue y en
  `deploy-service.sh`, y `DATABASE_URL` en asyncpg una vez desplegada la imagen nueva.
- **No se mueve dinero real.** La pasarela simulada no guarda datos de tarjeta: solo recibe el número de
  prueba y lo descarta.
- **El costo del boost y el catálogo de paquetes están en el código.** Cambiarlos es un deploy, y los
  movimientos ya hechos conservan el precio que tuvieron.

## 5. Estado de implementación

| Parte | Estado |
|---|---|
| Esquema, migraciones, `FOR UPDATE` documentado (§2.1 a §2.3) | Hecho (`alembic/versions/20261124_b7e2c4a91d30_initial_schema.py`) |
| Idempotencia (§2.4) | Hecha: comprar (`point_purchases`) y boostear (`idempotency_records`) |
| Comprar puntos, saldo e historial, saga y conciliación (§2.5, §2.6, §2.11) | Hecho (`tests/test_points_purchase.py`, `tests/test_purchase_saga.py`, `tests/test_payment_gateway.py`) |
| Boostear y retirar (§2.7) | Hecho (`tests/test_boosts.py`, `tests/test_server_roles_client.py`) |
| Ver nivel y beneficios | Hecho (`tests/test_boost_benefits.py`) |
| Publicador del outbox (§2.8) | Hecho, contenedor `payments-outbox-worker` (`tests/test_outbox.py`); compose de despliegue y `deploy-service.sh` actualizados |
| Regla de red infra → edge:8002 (§2.9) | Escrita en `infra/terraform/security.tf` y `SERVER_ROLES_URL` en el compose de despliegue (orquestador); falta el `terraform apply` |
| Aplicar beneficios en Server-Roles, Chat y clientes (§2.10) | Pendiente |
