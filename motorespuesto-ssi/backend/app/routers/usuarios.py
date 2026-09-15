from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, schemas
from app.auth import hash_password, require_root

router = APIRouter(prefix="/usuarios", tags=["Usuarios"])


@router.get("/", response_model=list[schemas.UsuarioOut], dependencies=[Depends(require_root)])
def listar_usuarios(db: Session = Depends(get_db)):
    return db.query(models.Usuario).all()


@router.get("/{usuario_id}", response_model=schemas.UsuarioOut, dependencies=[Depends(require_root)])
def obtener_usuario(usuario_id: int, db: Session = Depends(get_db)):
    user = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return user


@router.post("/", response_model=schemas.UsuarioOut, status_code=201, dependencies=[Depends(require_root)])
def crear_usuario(data: schemas.UsuarioCreate, db: Session = Depends(get_db)):
    if db.query(models.Usuario).filter(models.Usuario.username == data.username).first():
        raise HTTPException(status_code=400, detail="El username ya existe")
    nuevo = models.Usuario(
        username=data.username,
        password_hash=hash_password(data.password),
        nombre_real=data.nombre_real,
        rol=data.rol,
        local_id=data.local_id,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return nuevo


@router.patch("/{usuario_id}", response_model=schemas.UsuarioOut)
def actualizar_usuario(
    usuario_id: int,
    data: schemas.UsuarioUpdate,
    db: Session = Depends(get_db),
    actual: models.Usuario = Depends(require_root),
):
    user = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado")

    # No puedes modificar a OTRO root
    if user.rol == "root" and user.id != actual.id:
        raise HTTPException(403, "No puedes modificar a otro usuario root")

    update_data = data.model_dump(exclude_unset=True)

    # No puedes asignar rol root por esta vía
    if update_data.get("rol") == "root":
        raise HTTPException(403, "No se puede asignar el rol root por esta vía")

    # Si te modificas a ti mismo, ciertos campos quedan prohibidos
    if user.id == actual.id:
        for campo in ("rol", "activo", "local_id"):
            if campo in update_data:
                raise HTTPException(403, f"No puedes modificar '{campo}' de tu propia cuenta")

    # Validar local si viene
    if update_data.get("local_id") is not None:
        if not db.get(models.Local, update_data["local_id"]):
            raise HTTPException(400, "El local no existe")

    if "password" in update_data:
        user.password_hash = hash_password(update_data.pop("password"))
    for campo, valor in update_data.items():
        setattr(user, campo, valor)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Conflicto al actualizar")
    db.refresh(user)
    return user


@router.delete("/{usuario_id}", status_code=204)
def eliminar_usuario(
    usuario_id: int,
    db: Session = Depends(get_db),
    actual: models.Usuario = Depends(require_root),
):
    user = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not user:
        raise HTTPException(404, "Usuario no encontrado")

    if user.rol == "root":
        raise HTTPException(400, "No se puede eliminar un usuario root")

    if user.id == actual.id:
        raise HTTPException(400, "No puedes eliminarte a ti mismo")

    db.query(models.Venta).filter(models.Venta.usuario_id == usuario_id).update({"usuario_id": None})
    db.delete(user)
    db.commit()
