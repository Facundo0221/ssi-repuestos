"""
main.py — Servidor principal MotorRepuesto SSI
"""
import time, os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from app.database import engine, SessionLocal
from app import models
from app.routers import auth, usuarios, locales, categorias, productos, ventas, importar

STATIC_DIR = "/app/static/imagenes"
os.makedirs(STATIC_DIR, exist_ok=True)


def init_db(retries=10, delay=3):
    for attempt in range(1, retries + 1):
        try:
            models.Base.metadata.create_all(bind=engine)
            print("✓ Base de datos lista.")
            return
        except OperationalError:
            print(f"  Intento {attempt}/{retries} — reintentando en {delay}s...")
            time.sleep(delay)
    raise RuntimeError("No se pudo conectar a la base de datos.")


def migrate_db():
    """Agrega columnas/tablas nuevas sin borrar datos existentes."""
    migrations = [
        # v3 — columnas nuevas en productos
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS codigo_barras VARCHAR(100) UNIQUE",
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS stock_minimo INTEGER DEFAULT 0",
        # v5 — precios y margen
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS margen_individual NUMERIC(5,2)",
        "ALTER TABLE productos ADD COLUMN IF NOT EXISTS precio_costo NUMERIC(10,2)",
        "UPDATE productos SET precio_costo = precio WHERE precio_costo IS NULL",
        # v3 — anulación en ventas
        "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS anulada BOOLEAN DEFAULT FALSE",
        "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS anulada_motivo VARCHAR(255)",
        "ALTER TABLE ventas ADD COLUMN IF NOT EXISTS anulada_en TIMESTAMP",
        # subtotal en venta_items
        # subtotal — si existe como GENERATED, dropearlo y recrear como columna normal
        """DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='venta_items'
                AND column_name='subtotal'
                AND is_generated='ALWAYS'
            ) THEN
                ALTER TABLE venta_items DROP COLUMN subtotal;
            END IF;
        END $$""",
        "ALTER TABLE venta_items ADD COLUMN IF NOT EXISTS subtotal NUMERIC(10,2)",
        "UPDATE venta_items SET subtotal = cantidad * precio_unitario WHERE subtotal IS NULL",
        # v3 — tablas de auditoría
        """CREATE TABLE IF NOT EXISTS precio_historial (
            id              SERIAL PRIMARY KEY,
            producto_id     INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            precio_anterior NUMERIC(10,2) NOT NULL,
            precio_nuevo    NUMERIC(10,2) NOT NULL,
            usuario_id      INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
            creado_en       TIMESTAMP DEFAULT NOW()
        )""",
        # v6 — stock por local
        """CREATE TABLE IF NOT EXISTS stock_local (
            id          SERIAL PRIMARY KEY,
            producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            local_id    INTEGER NOT NULL REFERENCES locales(id)   ON DELETE CASCADE,
            cantidad    INTEGER NOT NULL DEFAULT 0,
            UNIQUE(producto_id, local_id)
        )""",
        """CREATE TABLE IF NOT EXISTS ajuste_stock (
            id            SERIAL PRIMARY KEY,
            producto_id   INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
            local_id      INTEGER REFERENCES locales(id)   ON DELETE SET NULL,
            usuario_id    INTEGER REFERENCES usuarios(id)  ON DELETE SET NULL,
            cantidad      INTEGER NOT NULL,
            stock_antes   INTEGER NOT NULL,
            stock_despues INTEGER NOT NULL,
            motivo        VARCHAR(255),
            creado_en     TIMESTAMP DEFAULT NOW()
        )""",
        # Poblar stock_local desde stock global si está vacío
        """INSERT INTO stock_local (producto_id, local_id, cantidad)
           SELECT p.id, l.id, p.stock
           FROM productos p
           CROSS JOIN (SELECT id FROM locales WHERE activo = true ORDER BY id LIMIT 1) l
           WHERE p.stock > 0
           AND NOT EXISTS (
               SELECT 1 FROM stock_local sl
               WHERE sl.producto_id = p.id AND sl.local_id = l.id
           )""",
    ]
    from sqlalchemy import text
    with engine.connect() as conn:
        for sql in migrations:
            try:
                conn.execute(text(sql))
            except Exception as e:
                print(f"  [migrate] skip: {e}")
        conn.commit()
    print("✓ Migraciones aplicadas.")


def seed_root_user():
    from app.auth import hash_password
    db = SessionLocal()
    try:
        if not db.query(models.Usuario).filter(models.Usuario.username == "root").first():
            pw = os.getenv("ROOT_PASSWORD", "root1234")
            db.add(models.Usuario(
                username="root", password_hash=hash_password(pw),
                nombre_real="Administrador", rol="root", activo=True,
            ))
            db.commit()
            print(f"✓ Usuario root creado (password desde ROOT_PASSWORD env)")
        else:
            print("✓ Usuario root ya existe.")
    finally:
        db.close()


init_db()
migrate_db()
seed_root_user()

# ── Allowed origins desde variable de entorno ─────────────────────────────────
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:5500,http://127.0.0.1:5500")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app = FastAPI(
    title="MotorRepuesto SSI",
    description="API de gestión para tienda de repuestos de motos — v0.6",
    version="0.6.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.mount("/static", StaticFiles(directory="/app/static"), name="static")

app.include_router(auth.router)
app.include_router(usuarios.router)
app.include_router(locales.router)
app.include_router(categorias.router)
app.include_router(productos.router)
app.include_router(ventas.router)
app.include_router(importar.router)


@app.get("/", tags=["Root"])
def root():
    return {"api": "MotorRepuesto SSI v0.6", "docs": "/docs"}