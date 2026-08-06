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


@router.patch("/{usuario_id}", response_model=schemas.UsuarioOut, dependencies=[Depends(require_root)])
def actualizar_usuario(usuario_id: int, data: schemas.UsuarioUpdate, db: Session = Depends(get_db)):
    user = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    update_data = data.model_dump(exclude_unset=True)
    if "password" in update_data:
        user.password_hash = hash_password(update_data.pop("password"))
    for campo, valor in update_data.items():
        setattr(user, campo, valor)
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{usuario_id}", status_code=204, dependencies=[Depends(require_root)])
def eliminar_usuario(usuario_id: int, db: Session = Depends(get_db)):
    user = db.query(models.Usuario).filter(models.Usuario.id == usuario_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user.rol == "root":
        raise HTTPException(status_code=400, detail="No se puede eliminar el usuario root")
    # Desvincular ventas antes de eliminar para no perder historial
    db.query(models.Venta).filter(models.Venta.usuario_id == usuario_id).update({"usuario_id": None})
    db.delete(user)
    db.commit()