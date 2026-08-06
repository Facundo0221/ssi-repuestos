-- ============================================
--  motorespuesto-ssi — Schema v4 (corregido)
--  Sincronizado con models.py
-- ============================================

-- 1) Funciones auxiliares
-- ============================================

CREATE OR REPLACE FUNCTION update_productos_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.actualizado_en = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION recalcular_stock_global()
RETURNS TRIGGER AS $$
DECLARE
    v_producto_id INTEGER;
BEGIN
    v_producto_id := COALESCE(NEW.producto_id, OLD.producto_id);

    UPDATE productos
    SET stock = COALESCE((
        SELECT SUM(cantidad) 
        FROM stock_local 
        WHERE producto_id = v_producto_id
    ), 0)
    WHERE id = v_producto_id;

    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;


-- 2) Tablas
-- ============================================

CREATE TABLE IF NOT EXISTS categorias (
    id          SERIAL PRIMARY KEY,
    nombre      VARCHAR(100) NOT NULL UNIQUE,
    descripcion TEXT,
    icono       VARCHAR(50),
    creado_en   TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS subcategorias (
    id           SERIAL PRIMARY KEY,
    categoria_id INTEGER NOT NULL REFERENCES categorias(id) ON DELETE CASCADE,
    nombre       VARCHAR(100) NOT NULL,
    creado_en    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS locales (
    id        SERIAL PRIMARY KEY,
    nombre    VARCHAR(100) NOT NULL,
    direccion VARCHAR(255),
    activo    BOOLEAN DEFAULT TRUE,
    creado_en TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS productos (
    id                SERIAL PRIMARY KEY,
    nombre            VARCHAR(250) NOT NULL,
    descripcion       TEXT,

    -- Precios
    precio            NUMERIC(10,2) NOT NULL,           -- precio de VENTA actual
    precio_costo      NUMERIC(10,2),                    -- precio de compra/importación
    precio_oferta     NUMERIC(10,2),                    -- precio especial temporal
    margen_individual NUMERIC(5,2),                     -- % margen aplicado

    -- Imagen
    imagen_url        VARCHAR(500),

    -- Clasificación
    subcategoria_id   INTEGER REFERENCES subcategorias(id) ON DELETE SET NULL,
    moto_110          BOOLEAN DEFAULT FALSE,
    moto_150          BOOLEAN DEFAULT FALSE,
    moto_200          BOOLEAN DEFAULT FALSE,
    marca             VARCHAR(100),
    medida            VARCHAR(50),
    codigo_barras     VARCHAR(100) UNIQUE,

    -- Stock global (suma de stock_local, mantenido por trigger)
    stock             INTEGER DEFAULT 0,
    stock_minimo      INTEGER DEFAULT 0,

    activo            BOOLEAN DEFAULT TRUE,
    creado_en         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    actualizado_en    TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Trigger para actualizar productos.actualizado_en en cada UPDATE
DROP TRIGGER IF EXISTS trigger_actualizar_productos ON productos;
CREATE TRIGGER trigger_actualizar_productos
BEFORE UPDATE ON productos
FOR EACH ROW
EXECUTE FUNCTION update_productos_timestamp();

CREATE TABLE IF NOT EXISTS stock_local (
    id          SERIAL PRIMARY KEY,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    local_id    INTEGER NOT NULL REFERENCES locales(id) ON DELETE CASCADE,
    cantidad    INTEGER NOT NULL DEFAULT 0 CHECK (cantidad >= 0),
    UNIQUE(producto_id, local_id)
);

-- Triggers para mantener productos.stock sincronizado con stock_local
DROP TRIGGER IF EXISTS trg_stock_local_insert ON stock_local;
CREATE TRIGGER trg_stock_local_insert
AFTER INSERT ON stock_local
FOR EACH ROW EXECUTE FUNCTION recalcular_stock_global();

DROP TRIGGER IF EXISTS trg_stock_local_update ON stock_local;
CREATE TRIGGER trg_stock_local_update
AFTER UPDATE ON stock_local
FOR EACH ROW EXECUTE FUNCTION recalcular_stock_global();

DROP TRIGGER IF EXISTS trg_stock_local_delete ON stock_local;
CREATE TRIGGER trg_stock_local_delete
AFTER DELETE ON stock_local
FOR EACH ROW EXECUTE FUNCTION recalcular_stock_global();

CREATE TABLE IF NOT EXISTS precio_historial (
    id              SERIAL PRIMARY KEY,
    producto_id     INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    precio_anterior NUMERIC(10,2) NOT NULL,
    precio_nuevo    NUMERIC(10,2) NOT NULL,
    usuario_id      INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    creado_en       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ajuste_stock (
    id            SERIAL PRIMARY KEY,
    producto_id   INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    local_id      INTEGER REFERENCES locales(id) ON DELETE SET NULL,
    cantidad      INTEGER NOT NULL,      -- positivo=entrada, negativo=salida
    stock_antes   INTEGER NOT NULL,
    stock_despues INTEGER NOT NULL,
    motivo        VARCHAR(255),
    usuario_id    INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    creado_en     TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS usuarios (
    id             SERIAL PRIMARY KEY,
    username       VARCHAR(50) NOT NULL UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,
    nombre_real    VARCHAR(100),
    rol            VARCHAR(20) NOT NULL DEFAULT 'vendedor'
                     CHECK (rol IN ('root','gerente','vendedor')),
    local_id       INTEGER REFERENCES locales(id) ON DELETE SET NULL,
    activo         BOOLEAN DEFAULT TRUE,
    creado_en      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ventas (
    id              SERIAL PRIMARY KEY,
    local_id        INTEGER NOT NULL REFERENCES locales(id) ON DELETE RESTRICT,
    usuario_id      INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    total           NUMERIC(10,2) NOT NULL DEFAULT 0,
    notas           TEXT,
    anulada         BOOLEAN DEFAULT FALSE,
    anulada_motivo  VARCHAR(255),
    anulada_en      TIMESTAMP WITH TIME ZONE,
    creado_en       TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS venta_items (
    id               SERIAL PRIMARY KEY,
    venta_id         INTEGER NOT NULL REFERENCES ventas(id) ON DELETE CASCADE,
    producto_id      INTEGER NOT NULL REFERENCES productos(id) ON DELETE RESTRICT,
    cantidad         INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario  NUMERIC(10,2) NOT NULL,
    subtotal         NUMERIC(10,2) GENERATED ALWAYS AS (cantidad * precio_unitario) STORED
);


-- 3) Seed data
-- ============================================

INSERT INTO locales (nombre, direccion) VALUES
    ('Local 1','Av. Principal 123'),
    ('Local 2','Calle Secundaria 456')
ON CONFLICT DO NOTHING;

INSERT INTO categorias (nombre, descripcion, icono) VALUES
    ('Motor y Componentes','Pistones, válvulas, juntas, aros','engine'),
    ('Electricidad','Baterías, sensores y sistema eléctrico','bolt'),
    ('Transmisión y Embrague','Kits, cadenas, discos','gear'),
    ('Frenos, Suspensión y Dirección','Pastillas, discos, amortiguadores','brake'),
    ('Carburación, Inyección y Admisión','Carburadores, filtros, cables','fuel'),
    ('Filtros','Filtros de aire, nafta y aceite','filter')
ON CONFLICT DO NOTHING;

INSERT INTO subcategorias (categoria_id, nombre)
SELECT id, unnest(ARRAY['Pistones','Válvulas','Juegos de juntas','Aros','Tapas de cilindro'])
FROM categorias WHERE nombre='Motor y Componentes';

INSERT INTO subcategorias (categoria_id, nombre)
SELECT id, unnest(ARRAY['Baterías','Sensores'])
FROM categorias WHERE nombre='Electricidad';

INSERT INTO subcategorias (categoria_id, nombre)
SELECT id, unnest(ARRAY['Kit de transmisión','Cadenas','Discos de embrague'])
FROM categorias WHERE nombre='Transmisión y Embrague';

INSERT INTO subcategorias (categoria_id, nombre)
SELECT id, unnest(ARRAY['Pastillas y zapatas','Discos de freno','Amortiguadores','Horquilla','Barrales','Rulemanes de dirección'])
FROM categorias WHERE nombre='Frenos, Suspensión y Dirección';

INSERT INTO subcategorias (categoria_id, nombre)
SELECT id, unnest(ARRAY['Carburadores','Cuerpo de mariposa','Filtros de aire','Múltiple de admisión','Cables de acelerador'])
FROM categorias WHERE nombre='Carburación, Inyección y Admisión';

INSERT INTO subcategorias (categoria_id, nombre)
SELECT id, unnest(ARRAY['Filtro de aire','Filtro de nafta','Filtro de aceite'])
FROM categorias WHERE nombre='Filtros';

INSERT INTO productos (nombre, descripcion, precio, precio_costo, precio_oferta, subcategoria_id, moto_110, moto_150, marca, stock, stock_minimo, codigo_barras)
SELECT 'Filtro de aire estándar 110/150','Compatible 110cc y 150cc',1500.00,900.00,1200.00,s.id,TRUE,TRUE,'Estándar',0,3,'7790001100001'
FROM subcategorias s JOIN categorias c ON s.categoria_id=c.id
WHERE c.nombre='Filtros' AND s.nombre='Filtro de aire' LIMIT 1;

INSERT INTO productos (nombre, descripcion, precio, precio_costo, subcategoria_id, moto_150, marca, stock, stock_minimo, codigo_barras)
SELECT 'Kit de pistón STD 150','Kit completo pistón+aros',4200.00,2500.00,s.id,TRUE,'Premium',0,2,'7790001500001'
FROM subcategorias s JOIN categorias c ON s.categoria_id=c.id
WHERE c.nombre='Motor y Componentes' AND s.nombre='Pistones' LIMIT 1;

INSERT INTO productos (nombre, descripcion, precio, precio_costo, subcategoria_id, moto_110, moto_150, moto_200, marca, stock, stock_minimo, codigo_barras)
SELECT 'Batería 12V 5Ah','Batería sellada de larga duración',8500.00,5000.00,s.id,TRUE,TRUE,TRUE,'Yuasa',0,2,'7790001200001'
FROM subcategorias s JOIN categorias c ON s.categoria_id=c.id
WHERE c.nombre='Electricidad' AND s.nombre='Baterías' LIMIT 1;

-- Insertar stock inicial en stock_local (para que los triggers calculen productos.stock)
INSERT INTO stock_local (producto_id, local_id, cantidad)
SELECT p.id, l.id, 10
FROM productos p, locales l
WHERE p.nombre = 'Filtro de aire estándar 110/150';

INSERT INTO stock_local (producto_id, local_id, cantidad)
SELECT p.id, l.id, 5
FROM productos p, locales l
WHERE p.nombre = 'Kit de pistón STD 150';

INSERT INTO stock_local (producto_id, local_id, cantidad)
SELECT p.id, l.id, 8
FROM productos p, locales l
WHERE p.nombre = 'Batería 12V 5Ah';