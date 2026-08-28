from pathlib import Path
import re
from time import monotonic
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from .database import SessionLocal
from .categories import BLOG_CATEGORIES
from .models import Comment, Post, PostImage, User

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
UPLOAD_DIRECTORY = Path(__file__).resolve().parent / "static" / "uploads"
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
MAX_IMAGE_SIZE = 5 * 1024 * 1024
INLINE_IMAGE_PATH_PATTERN = re.compile(r"!\[[^\]]*\]\((/static/uploads/[A-Za-z0-9._-]+)\)")
MAX_LOGIN_ATTEMPTS = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 15 * 60
failed_login_attempts = {}


async def save_image(upload: UploadFile) -> str:
    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Images must be JPG, PNG, or WebP files")

    contents = await upload.read()
    if len(contents) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="Each image must be 5 MB or smaller")

    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{ALLOWED_IMAGE_TYPES[upload.content_type]}"
    (UPLOAD_DIRECTORY / filename).write_bytes(contents)
    return f"/static/uploads/{filename}"


def remove_local_image(image_path: str) -> None:
    prefix = "/static/uploads/"
    if image_path.startswith(prefix):
        (UPLOAD_DIRECTORY / image_path.removeprefix(prefix)).unlink(missing_ok=True)


def inline_image_paths(body: str) -> set[str]:
    return set(INLINE_IMAGE_PATH_PATTERN.findall(body))


def validate_category(category: str) -> str:
    if category not in BLOG_CATEGORIES:
        raise HTTPException(status_code=422, detail="Please choose a valid category")
    return category


def login_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def ensure_login_not_locked(request: Request) -> str:
    client_key = login_client_key(request)
    attempt = failed_login_attempts.get(client_key)
    if not attempt:
        return client_key

    now = monotonic()
    if attempt["locked_until"] > now:
        retry_after = int(attempt["locked_until"] - now) + 1
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
    if attempt["locked_until"] or now - attempt["first_failure"] > LOGIN_ATTEMPT_WINDOW_SECONDS:
        del failed_login_attempts[client_key]
    return client_key


def record_failed_login(client_key: str) -> None:
    now = monotonic()
    attempt = failed_login_attempts.get(client_key)
    if not attempt:
        attempt = {"count": 0, "first_failure": now, "locked_until": 0}
        failed_login_attempts[client_key] = attempt

    attempt["count"] += 1
    if attempt["count"] >= MAX_LOGIN_ATTEMPTS:
        attempt["locked_until"] = now + LOGIN_LOCKOUT_SECONDS


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
    finally:
        db.close()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="You don't have permission to access this page")
    return current_user


@router.get("")
def admin(
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    posts = db.query(Post).order_by(Post.created_at.desc()).all()
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "posts": posts,
            "message": request.query_params.get("message"),
        },
    )


@router.get("/login")
def login_page(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    client_key = ensure_login_not_locked(request)
    user = db.query(User).filter(User.username == username).first()
    if (
        not user
        or user.role != "admin"
        or not pwd_context.verify(password, user.password_hash)
    ):
        record_failed_login(client_key)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    failed_login_attempts.pop(client_key, None)
    request.session["user_id"] = user.id
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/uploads/inline")
async def upload_inline_image(
    image: UploadFile = File(...),
    current_user: User = Depends(require_admin),
):
    return {"image_path": await save_image(image)}


@router.post("/posts/create")
async def create_post(
    title: str = Form(...),
    category: str = Form(...),
    excerpt: str = Form(...),
    body: str = Form(...),
    image: str = Form(""),
    cover_upload: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    category = validate_category(category)
    cover_image = image.strip() or "/static/images/blog1.jpg"
    if cover_upload and cover_upload.filename:
        cover_image = await save_image(cover_upload)

    post = Post(
        title=title,
        category=category,
        excerpt=excerpt,
        body=body,
        image=cover_image,
        author_id=current_user.id,
    )
    db.add(post)
    db.commit()
    return RedirectResponse(url="/admin?message=Post+published", status_code=303)


@router.get("/posts/new")
def new_post_page(request: Request, current_user: User = Depends(require_admin)):
    return templates.TemplateResponse(
        "admin/post_form.html", {"request": request, "post": None, "categories": BLOG_CATEGORIES}
    )


@router.get("/posts/{post_id}/edit")
def edit_post_page(
    post_id: int,
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return templates.TemplateResponse(
        "admin/post_form.html", {"request": request, "post": post, "categories": BLOG_CATEGORIES}
    )


@router.post("/posts/{post_id}/edit")
async def update_post(
    post_id: int,
    title: str = Form(...),
    category: str = Form(...),
    excerpt: str = Form(...),
    body: str = Form(...),
    image: str = Form(""),
    cover_upload: Optional[UploadFile] = File(None),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    category = validate_category(category)
    removed_inline_images = inline_image_paths(post.body) - inline_image_paths(body)
    post.title = title
    post.category = category
    post.excerpt = excerpt
    post.body = body
    if cover_upload and cover_upload.filename:
        remove_local_image(post.image)
        post.image = await save_image(cover_upload)
    elif image.strip():
        post.image = image.strip()

    db.commit()
    for image_path in removed_inline_images:
        remove_local_image(image_path)
    return RedirectResponse(url="/admin?message=Post+updated", status_code=303)


@router.post("/posts/{post_id}/delete")
def delete_post(
    post_id: int,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    images = db.query(PostImage).filter(PostImage.post_id == post_id).all()
    for image in images:
        remove_local_image(image.image_path)
    remove_local_image(post.image)
    for image_path in inline_image_paths(post.body):
        remove_local_image(image_path)
    db.query(PostImage).filter(PostImage.post_id == post_id).delete()
    db.query(Comment).filter(Comment.post_id == post_id).delete()
    db.delete(post)
    db.commit()
    return RedirectResponse(url="/admin?message=Post+deleted", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)
