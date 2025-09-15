from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import instaloader
from slowapi import Limiter
from slowapi.util import get_remote_address
from cachetools import TTLCache
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import httpx
import logging
import time

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI init
app = FastAPI(title="Instagram Profile Fetcher",
              description="Fetch Instagram profile info safely")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Rate Limiting
limiter = Limiter(key_func=get_remote_address)

# TTL Cache: 1 hour
profile_cache = TTLCache(maxsize=1000, ttl=3600)

# Templates & Static (For homepage)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# ----------------------
# 📦 Models
# ----------------------
class ProfileData(BaseModel):
    username: str
    followers: int
    following: int
    posts_count: int
    dp_url: str
    bio: str = None
    full_name: str = None

class ErrorResponse(BaseModel):
    error: str
    details: str = None

# ----------------------
# ⚙️ Helper: Instaloader client
# ----------------------
def get_instagram_client():
    L = instaloader.Instaloader(
        quiet=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
        max_connection_attempts=3
    )
    L.request_timeout = 30
    L.sleep = True
    L.save_metadata = False
    L.download_comments = False
    L.download_geotags = False
    L.download_pictures = False
    return L

# ----------------------
# 🚀 Main API Endpoint
# ----------------------
@app.get("/scrape/{username}",
         response_model=ProfileData,
         responses={
             404: {"model": ErrorResponse},
             429: {"model": ErrorResponse},
             500: {"model": ErrorResponse}
         })
@limiter.limit("10/minute")
async def get_instagram_profile(request: Request, username: str):
    try:
        # Cache check
        if username.lower() in profile_cache:
            logger.info(f"[CACHE] {username}")
            return profile_cache[username.lower()]

        logger.info(f"[FETCH] {username}")
        start_time = time.time()
        L = get_instagram_client()

        # Profile fetch
        try:
            profile = instaloader.Profile.from_username(L.context, username.lower())
        except instaloader.exceptions.ProfileNotExistsException:
            raise HTTPException(
                status_code=404,
                detail={"error": "Profile not found", "details": f"@{username} doesn't exist"}
            )

        if not profile.userid:
            raise HTTPException(
                status_code=404,
                detail={"error": "Profile not found", "details": "Invalid data from Instagram"}
            )

        profile_data = ProfileData(
            username=profile.username,
            followers=profile.followers,
            following=profile.followees,
            posts_count=profile.mediacount,
            dp_url=profile.profile_pic_url,
            bio=profile.biography,
            full_name=profile.full_name
        )

        # Cache result
        profile_cache[username.lower()] = profile_data
        logger.info(f"[DONE] {username} in {time.time()-start_time:.2f}s")
        return profile_data

    except instaloader.exceptions.ConnectionException as e:
        logger.error(f"Connection error: {str(e)}")
        raise HTTPException(
            status_code=503,
            detail={"error": "Instagram connection failed", "details": str(e)}
        )
    except instaloader.exceptions.InstaloaderException as e:
        logger.error(f"Instaloader error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={"error": "Instagram fetch failed", "details": str(e)}
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={"error": "Internal error", "details": str(e)}
        )

@app.get("/proxy-image/")
async def proxy_image(url: str):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
            "ngrok-skip-browser-warning": "true"
        }
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            return StreamingResponse(
                resp.iter_bytes(),
                media_type=resp.headers.get("Content-Type", "image/jpeg")
            )
    except Exception as e:
        logger.error(f"Error proxying image: {e}")
        raise HTTPException(status_code=500, detail="Image fetch failed")

    
# ----------------------
# 🧪 Health Check
# ----------------------
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "cache_size": len(profile_cache)
    }

# ----------------------
# 🔥 Run app (Local dev only)
# ----------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8090)

