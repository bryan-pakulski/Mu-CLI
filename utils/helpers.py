# Mime type guessing, image terminal display
import mimetypes
import os
import time
import base64
import subprocess
import shutil
import io
from rich.console import Console
from .config import HAS_PIL, SESSION_DIR

# Need local PIL import logic for helper
if HAS_PIL:
    from PIL import Image

console = Console()


def get_safe_mime_type(file_path):
    mime_type, _ = mimetypes.guess_type(file_path)
    try:
        if mime_type is not None:
            if (
                mime_type.startswith("image/")
                or mime_type.startswith("audio/")
                or mime_type.startswith("video/")
                or mime_type == "application/pdf"
            ):
                return mime_type
    except Exception:
        return "text/plain"

    return "text/plain"


def display_image_in_terminal(
    session_id, image_data, mime_type="image/png", save=False
):
    """
    Saves image to disk and attempts to display it inline using CLI protocols.
    """
    if not HAS_PIL:
        console.print("[yellow]PIL not installed. Cannot process image bytes.[/yellow]")
        return

    timestamp = int(time.time())
    ext = mimetypes.guess_extension(mime_type) or ".png"
    filename = f"img_{timestamp}{ext}"

    # Images should be saved under ~/.mucli/sessions/<id>/images
    filepath = os.path.join(SESSION_DIR, session_id, "images", filename)

    try:
        image = Image.open(io.BytesIO(image_data))
        if save:
            # Save to disk
            image.save(filepath)

            # 1. Print clickable link
            file_url = f"file://{filepath}"
            console.print(
                f"\n[bold cyan]Image Generated:[/bold cyan] [link={file_url}]{filepath}[/link]"
            )

        # 2. Attempt Inline Display
        term = os.environ.get("TERM_PROGRAM", "")
        if "iTerm" in term:
            b64_data = base64.b64encode(image_data).decode("ascii")
            print(f"\033]1337;File=inline=1:{b64_data}\a")
            return

        if "kitty" in term:
            try:
                subprocess.run(["kitty", "+kitten", "icat", filepath], check=False)
                return
            except FileNotFoundError:
                pass

        if shutil.which("timg"):
            subprocess.run(["timg", "-g", "100x100", filepath], check=False)
        elif shutil.which("catimg"):
            subprocess.run(["catimg", "-w", "100", filepath], check=False)
        else:
            console.print(f"[dim](Image size: {image.size[0]}x{image.size[1]})[/dim]")

    except Exception as e:
        console.print(f"[red]Failed to display image: {e}[/red]")
