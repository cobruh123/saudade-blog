import re
import os
import secrets
import warnings
from html import escape

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from starlette.middleware.sessions import SessionMiddleware
from markupsafe import Markup

from .database import Base, engine, SessionLocal
from .categories import BLOG_CATEGORIES
from .models import Post, Comment
from .admin import router as admin_router

app = FastAPI()
Base.metadata.create_all(bind=engine)
session_secret = os.getenv("SESSION_SECRET")
if not session_secret:
    session_secret = secrets.token_urlsafe(32)
    warnings.warn(
        "SESSION_SECRET is not set. Sessions will be invalidated when the server restarts.",
        RuntimeWarning,
    )

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static"
)

app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret,
    https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
    same_site="lax",
)

templates = Jinja2Templates(directory="app/templates")
INLINE_IMAGE_PATTERN = re.compile(
    r"^!\[([^\]]*)\]\((/static/(?:uploads|images)/[A-Za-z0-9._/-]+)\)$"
)


def format_post_body(body: str) -> Markup:
    """Render a small, safe subset of Markdown used by the writer toolbar."""
    def inline(text: str) -> str:
        text = escape(text)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
        return re.sub(
            r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
            r'<a href="\2" rel="noopener noreferrer" target="_blank">\1</a>',
            text,
        )

    blocks = []
    list_items = []

    def close_list():
        if list_items:
            blocks.append("<ul>" + "".join(list_items) + "</ul>")
            list_items.clear()

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            list_items.append(f"<li>{inline(stripped[2:])}</li>")
            continue

        close_list()
        if not stripped:
            continue
        inline_image = INLINE_IMAGE_PATTERN.match(stripped)
        if inline_image:
            alt_text, image_path = inline_image.groups()
            caption = f"<figcaption>{escape(alt_text)}</figcaption>" if alt_text else ""
            blocks.append(
                f'<figure class="inline-post-image"><img src="{image_path}" alt="{escape(alt_text)}">{caption}</figure>'
            )
        elif stripped == "---":
            blocks.append("<hr>")
        elif stripped.startswith("## "):
            blocks.append(f"<h2>{inline(stripped[3:])}</h2>")
        elif stripped.startswith("### "):
            blocks.append(f"<h3>{inline(stripped[4:])}</h3>")
        elif stripped.startswith("> "):
            blocks.append(f"<blockquote>{inline(stripped[2:])}</blockquote>")
        else:
            blocks.append(f"<p>{inline(stripped)}</p>")

    close_list()
    return Markup("\n".join(blocks))


templates.env.filters["format_post_body"] = format_post_body

@app.get("/")
def home(request: Request):

    db = SessionLocal()

    posts = db.query(Post).order_by(Post.created_at.desc()).limit(3).all()
    
    db.close()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "posts": posts
        }
    )

@app.get("/blog")
def blog_page(request: Request):
    db = SessionLocal()
    requested_category = request.query_params.get("category")
    selected_category = requested_category if requested_category in BLOG_CATEGORIES else None

    posts_query = db.query(Post)
    if selected_category:
        posts_query = posts_query.filter(Post.category == selected_category)
    posts = posts_query.order_by(Post.created_at.desc()).all()
    category_counts = dict(
        db.query(Post.category, func.count(Post.id))
        .group_by(Post.category)
        .all()
    )

    db.close()

    return templates.TemplateResponse(
        "blog.html",
        {
            "request": request,
            "posts": posts,
            "categories": BLOG_CATEGORIES,
            "category_counts": category_counts,
            "selected_category": selected_category,
        }
    )

@app.get("/blog/{post_id}")
def read_post(
    post_id: int,
    request: Request
):
    db = SessionLocal()

    post = db.query(Post).filter(
        Post.id == post_id
    ).first()

    comments = db.query(Comment).filter(
        Comment.post_id == post_id
    ).order_by(
        Comment.created_at.desc()
    ).all()

    db.close()

    if not post:
        raise HTTPException(
            status_code=404,
            detail="Post not found"
        )

    return templates.TemplateResponse(
        "post.html",
        {
            "request": request,
            "post": post,
            "comments": comments,
        }
    )

@app.post("/blog/{post_id}/comments")
def create_comment(
    post_id: int,
    author_name: str = Form(...),
    content: str = Form(...)
):

    db = SessionLocal()

    comment = Comment(
        post_id=post_id,
        author_name=author_name,
        content=content
    )

    db.add(comment)
    db.commit()
    db.close()

    return RedirectResponse(
        url=f"/blog/{post_id}",
        status_code=303
    )

# Exception handler for HTTPException to render a custom error page
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": exc.status_code,
            "detail": exc.detail,
        },
        status_code=exc.status_code,
    )

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status_code": 404,
            "detail": "Page not found",
        },
        status_code=404,
    )

app.include_router(admin_router)
