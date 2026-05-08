import logging
import time

# Python's basicConfig is the equivalent of configuring ILogger in Program.cs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Scrapers service started. No jobs scheduled yet.")

    # Keep the container alive. APScheduler jobs will be registered here in Phase 1.
    # Unlike C# hosted services, there's no built-in runtime loop — we drive it ourselves.
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
