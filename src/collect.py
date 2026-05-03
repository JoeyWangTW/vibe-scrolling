"""Unified entry point for multi-platform feed collection."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load_config() -> dict:
    """Load configuration from config.json."""
    config_path = Path("config.json")
    if not config_path.exists():
        print("[collect] config.json not found, using defaults")
        return {"output_dir": "feed_data", "platforms": {"x": {"enabled": True}}}
    return json.loads(config_path.read_text())


async def run_platform(platform: str, config: dict):
    """Run a single platform's collector."""
    if platform == "x":
        from src.platforms.x.collector import run
        return await run(config)
    elif platform == "threads":
        from src.platforms.threads.collector import run
        return await run(config)
    elif platform == "instagram":
        from src.platforms.instagram.collector import run
        return await run(config)
    elif platform == "youtube":
        from src.platforms.youtube.collector import run
        return await run(config)
    elif platform == "linkedin":
        from src.platforms.linkedin.collector import run
        return await run(config)
    else:
        print(f"[collect] Unknown platform: {platform}")
        return None


async def main():
    parser = argparse.ArgumentParser(description="Feed Collector")
    parser.add_argument(
        "--platform", "-p",
        choices=["x", "threads", "instagram", "youtube", "linkedin"],
        help="Run a specific platform (default: all enabled)",
    )
    args = parser.parse_args()

    config = load_config()
    platforms_config = config.get("platforms", {})

    if args.platform:
        # Run single platform
        print(f"[collect] Running {args.platform} collector...")
        await run_platform(args.platform, config)
    else:
        # Run all enabled platforms
        for platform, pconfig in platforms_config.items():
            if pconfig.get("enabled", False):
                print(f"\n[collect] Running {platform} collector...")
                await run_platform(platform, config)

    print("\n[collect] Done.")


if __name__ == "__main__":
    asyncio.run(main())
