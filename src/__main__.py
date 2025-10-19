from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import traceback
import warnings
from logging.handlers import TimedRotatingFileHandler

import truststore


if __name__ == "__main__":

    truststore.inject_into_ssl()

    from src.config import FILE_FORMATTER, LOGGING_LEVELS
    from src.config.settings import Settings
    from src.core.client import Twitch
    from src.exceptions import CaptchaRequired
    from src.i18n import _
    from src.version import __version__

    logger = logging.getLogger("TwitchDrops")
    # Force INFO level logging by default for better visibility
    logger.setLevel(logging.INFO)
    if logger.level < logging.INFO:
        logger.setLevel(logging.INFO)
    # Always add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(FILE_FORMATTER)
    logger.addHandler(console_handler)
    logger.info("Logger initialized")

    warnings.simplefilter("default", ResourceWarning)

    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or higher is required")

    class ParsedArgs(argparse.Namespace):
        _verbose: int
        _debug_ws: bool
        _debug_gql: bool
        log: bool
        dump: bool

        # TODO: replace int with union of literal values once typeshed updates
        @property
        def logging_level(self) -> int:
            return LOGGING_LEVELS[min(self._verbose, 4)]

        @property
        def debug_ws(self) -> int:
            """
            If the debug flag is True, return DEBUG.
            If the main logging level is DEBUG, return INFO to avoid seeing raw messages.
            Otherwise, return NOTSET to inherit the global logging level.
            """
            if self._debug_ws:
                return logging.DEBUG
            elif self._verbose >= 4:
                return logging.INFO
            return logging.NOTSET

        @property
        def debug_gql(self) -> int:
            if self._debug_gql:
                return logging.DEBUG
            elif self._verbose >= 4:
                return logging.INFO
            return logging.NOTSET

    # handle input parameters
    logger.debug("Parsing command line arguments")
    parser = argparse.ArgumentParser(
        description="A program that allows you to mine timed drops on Twitch.",
    )
    parser.add_argument("--version", action="version", version=f"v{__version__}")
    parser.add_argument("-v", dest="_verbose", action="count", default=0)
    parser.add_argument("--dump", action="store_true")
    # undocumented debug args
    parser.add_argument("--debug-ws", dest="_debug_ws", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--debug-gql", dest="_debug_gql", action="store_true", help=argparse.SUPPRESS
    )
    logger.debug("Parsing arguments into ParsedArgs namespace")
    args = parser.parse_args(namespace=ParsedArgs())
    # load settings
    logger.debug("Loading settings")
    try:
        settings = Settings(args)
    except Exception:
        logger.exception("Error while loading settings")
        print(f"Settings error: {traceback.format_exc()}", file=sys.stderr)
        sys.exit(4)

    # client run
    async def main():
        # set language
        from contextlib import suppress

        with suppress(ValueError):
            # this language doesn't exist - stick to English
            _.set_language(settings.language)

        # Always log to file with timestamped filename in ./logs/ directory
        from pathlib import Path

        # Create logs directory if it doesn't exist
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / "TDM.log"

        # Add file handler for timestamped log
        file_handler = TimedRotatingFileHandler(log_file, when="midnight", backupCount=5)
        file_handler.setFormatter(FILE_FORMATTER)
        logger.addHandler(file_handler)
        logger.info(f"Logging to file: {log_file}")

        logging.getLogger("TwitchDrops.gql").setLevel(settings.debug_gql)
        logging.getLogger("TwitchDrops.websocket").setLevel(settings.debug_ws)

        logger.info("=== TwitchDropsMiner Starting ===")
        logger.info(f"Version: {__version__}")
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Platform: {sys.platform}")

        exit_status = 0
        logger.info("Creating Twitch client")
        client = Twitch(settings)

        # Initialize web GUI
        logger.info("Initializing web GUI mode")
        from src.web import app as webapp
        from src.web.gui_manager import WebGUIManager

        # Set up web GUI
        logger.debug("Creating WebGUIManager")
        client.gui = WebGUIManager(client)
        # Set up webapp references
        logger.debug("Setting up webapp managers")
        webapp.set_managers(client.gui, client)
        # Start web server in background
        logger.info("Starting web server on http://0.0.0.0:8080")
        web_server_task = asyncio.create_task(webapp.run_server(host="0.0.0.0", port=8080))
        logger.info("Web server task created")

        loop = asyncio.get_running_loop()
        if sys.platform == "linux":
            logger.debug("Setting up signal handlers for SIGINT and SIGTERM")
            loop.add_signal_handler(signal.SIGINT, lambda *_: client.close())
            loop.add_signal_handler(signal.SIGTERM, lambda *_: client.close())

        logger.info("Starting main client run loop")
        try:
            await client.run()
            logger.info("Client run completed normally")
        except CaptchaRequired:
            logger.error("Captcha required - cannot continue")
            exit_status = 1
            client.print(_("error", "captcha"))
        except Exception:
            logger.exception("Fatal error encountered during client run")
            exit_status = 1
            client.print("Fatal error encountered:\n")
            client.print(traceback.format_exc())
        finally:
            logger.info("=== Starting shutdown sequence ===")
            if sys.platform == "linux":
                logger.debug("Removing signal handlers (Linux)")
                loop.remove_signal_handler(signal.SIGINT)
                loop.remove_signal_handler(signal.SIGTERM)
            logger.info("Notifying client of exit")
            client.print(_("gui", "status", "exiting"))
            # Shutdown web server
            if web_server_task and not web_server_task.done():
                logger.info("Shutting down web server")
                # Trigger graceful shutdown and wait for it to finish
                await webapp.shutdown_server()
                # Wait for server to actually exit (with timeout)
                try:
                    await asyncio.wait_for(web_server_task, timeout=5.0)
                    logger.info("Web server task completed gracefully")
                except asyncio.TimeoutError:
                    logger.warning("Web server didn't exit in time, forcing cancellation")
                    web_server_task.cancel()
                    try:
                        await web_server_task
                    except asyncio.CancelledError:
                        logger.info("Web server task force-cancelled")
                except Exception as e:
                    logger.error(f"Error while shutting down web server: {e}")
            else:
                logger.debug(
                    f"Web server task status: task={web_server_task is not None}, done={web_server_task.done() if web_server_task else 'N/A'}"
                )
            logger.info("Shutting down Twitch client")
            await client.shutdown()
            logger.info("Twitch client shutdown completed")
        logger.info(f"Shutdown complete - exit_status={exit_status}")
        if exit_status != 0:
            logger.warning("Application terminated with error - showing error state")
            # Application terminated with error
            client.print(_("status", "terminated"))
            client.gui.status.update(_("gui", "status", "terminated"))
            # notify the user about the closure
            client.gui.grab_attention(sound=True)
            # Web GUI doesn't need to wait - browser clients can stay connected
            logger.info("Web GUI - no need to wait for user to close browser")
        else:
            logger.info("Normal shutdown - proceeding")
        # save the application state
        logger.info("Saving application state")
        client.save(force=True)
        logger.info("Application state saved")
        logger.info(f"=== Exiting with status code: {exit_status} ===")
        sys.exit(exit_status)

    asyncio.run(main())

