from __future__ import annotations

# import an additional thing for proper PyInstaller freeze support
from multiprocessing import freeze_support


if __name__ == "__main__":
    freeze_support()
    import sys
    import signal
    import asyncio
    import logging
    import argparse
    import warnings
    import traceback
    from typing import NoReturn

    import truststore
    truststore.inject_into_ssl()

    from src.i18n import _
    from src.core.client import Twitch
    from src.config.settings import Settings
    from src.version import __version__
    from src.exceptions import CaptchaRequired
    from src.config.paths import _resource_path as resource_path
    from src.config import LOGGING_LEVELS, SELF_PATH, FILE_FORMATTER, LOG_PATH, LOCK_PATH


    logger = logging.getLogger("TwitchDrops")
    # Force INFO level logging by default for better visibility
    logger.setLevel(logging.DEBUG)
    # Always add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(FILE_FORMATTER)
    logger.addHandler(console_handler)
    logger.info("Logger initialized")

    warnings.simplefilter("default", ResourceWarning)

    # import tracemalloc
    # tracemalloc.start(3)

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
        SELF_PATH.name,
        description="A program that allows you to mine timed drops on Twitch.",
    )
    parser.add_argument("--version", action="version", version=f"v{__version__}")
    parser.add_argument("-v", dest="_verbose", action="count", default=0)
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--dump", action="store_true")
    # undocumented debug args
    parser.add_argument(
        "--debug-ws", dest="_debug_ws", action="store_true", help=argparse.SUPPRESS
    )
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
    logger.debug("Defining main async function")
    async def main():
        # set language
        try:
            _.set_language(settings.language)
        except ValueError:
            # this language doesn't exist - stick to English
            pass

        # Always log to file with timestamped filename in ./logs/ directory
        from datetime import datetime
        from pathlib import Path

        # Create logs directory if it doesn't exist
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)

        # Generate timestamped log filename: TDM.YYYY-MM-DDTHH-MM-SS.log
        timestamp = datetime.now().isoformat(timespec='seconds').replace(':', '-')
        log_file = logs_dir / f"TDM.{timestamp}.log"

        # Add file handler for timestamped log
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(FILE_FORMATTER)
        logger.addHandler(file_handler)
        logger.info(f"Logging to file: {log_file}")

        # Keep old log.txt for backward compatibility if --log flag is used
        if settings.log:
            legacy_handler = logging.FileHandler(LOG_PATH)
            legacy_handler.setFormatter(FILE_FORMATTER)
            logger.addHandler(legacy_handler)
            logger.info(f"Legacy log file: {LOG_PATH}")

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
        web_server_task = asyncio.create_task(
            webapp.run_server(host="0.0.0.0", port=8080)
        )
        logger.info("Web server task created")

        loop = asyncio.get_running_loop()
        if sys.platform == "linux":
            logger.debug("Setting up signal handlers for SIGINT and SIGTERM")
            loop.add_signal_handler(signal.SIGINT, lambda *_: client.gui.close())
            loop.add_signal_handler(signal.SIGTERM, lambda *_: client.gui.close())

        logger.info("Starting main client run loop")
        try:
            await client.run()
            logger.info("Client run completed normally")
        except CaptchaRequired:
            logger.error("Captcha required - cannot continue")
            exit_status = 1
            client.prevent_close()
            client.print(_("error", "captcha"))
        except Exception:
            logger.exception("Fatal error encountered during client run")
            exit_status = 1
            client.prevent_close()
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
                logger.debug(f"Web server task status: task={web_server_task is not None}, done={web_server_task.done() if web_server_task else 'N/A'}")
            logger.info("Shutting down Twitch client")
            await client.shutdown()
            logger.info("Twitch client shutdown completed")
        logger.info(f"Shutdown complete - close_requested={client.gui.close_requested}, exit_status={exit_status}")
        if not client.gui.close_requested:
            logger.warning("User didn't request closure - showing error state")
            # user didn't request the closure
            client.gui.tray.change_icon("error")
            client.print(_("status", "terminated"))
            client.gui.status.update(_("gui", "status", "terminated"))
            # notify the user about the closure
            client.gui.grab_attention(sound=True)
            # Wait for user to close the GUI window (only needed if close wasn't already requested)
            logger.info("Waiting for GUI to close")
            await client.gui.wait_until_closed()
            logger.info("GUI closed by user")
        else:
            logger.info("Close already requested - skipping GUI wait")
        # save the application state
        # NOTE: we have to do it after wait_until_closed,
        # because the user can alter some settings between app termination and closing the window
        logger.info("Saving application state")
        client.save(force=True)
        logger.info("Application state saved")
        logger.info("Stopping GUI")
        client.gui.stop()
        logger.info("GUI stopped")
        logger.info("Closing GUI window")
        client.gui.close_window()
        logger.info(f"=== Exiting with status code: {exit_status} ===")
        sys.exit(exit_status)

    asyncio.run(main())
    # try:
    #     # use lock_file to check if we're not already running
    #     success, file = lock_file(LOCK_PATH)
    #     if not success:
    #         # already running - exit
    #         sys.exit(3)

    #     asyncio.run(main())
    # finally:
    #     file.close()
