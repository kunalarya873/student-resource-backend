import atexit
import logging
import os
import platform
import signal
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import Any

from app.core.config import settings

logger = logging.getLogger("amu_backend.lifecycle")
crash_logger = logging.getLogger("amu_backend.crash")

_lifecycle_initialized = False
_server_boot_time: datetime | None = None
_server_boot_perf: float | None = None
_shutdown_recorded = False


def _format_uptime(seconds: float) -> str:
    """Format duration in seconds into human-readable string: e.g. '2d 4h 12m 30s' or '45.2s'."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{sec}s")
    return " ".join(parts)


class ServerLifecycleTracker:
    """
    Monitors and logs server lifecycle events with distinct Dev and Prod behaviors:
    - Dev Mode: High-readability console, Ctrl+C recognized as normal dev stop, silenced noisy third-party libraries.
    - Prod Mode: Strict structured format, container orchestrator signal handling, critical crash alerts to server_crash.log.
    """

    @classmethod
    def get_boot_time(cls) -> datetime | None:
        return _server_boot_time

    @classmethod
    def get_uptime_seconds(cls) -> float:
        if _server_boot_perf is None:
            return 0.0
        return round(time.perf_counter() - _server_boot_perf, 2)

    @classmethod
    def get_uptime_human(cls) -> str:
        return _format_uptime(cls.get_uptime_seconds())

    @classmethod
    def record_startup(cls, app_name: str | None = None, env: str | None = None) -> None:
        global _server_boot_time, _server_boot_perf, _shutdown_recorded
        _server_boot_time = datetime.now(UTC)
        _server_boot_perf = time.perf_counter()
        _shutdown_recorded = False

        app_name = app_name or settings.APP_NAME
        env = env or settings.APP_ENV
        pid = os.getpid()

        if settings.is_development:
            banner = (
                "\n" + "-" * 70 + "\n"
                f"🚀 [DEV SERVER STARTUP] {app_name}\n"
                f"   • Started At : {_server_boot_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"   • Mode       : DEVELOPMENT (Auto-Reload Active)\n"
                f"   • Process ID : {pid}\n"
                f"   • Python     : {platform.python_version()} on {platform.system()}\n"
                f"   • Log File   : {settings.LOG_DIR}/{settings.SERVER_LOG_FILE}\n"
                + "-" * 70
            )
        else:
            banner = (
                "\n" + "=" * 80 + "\n"
                f"🚀 [PRODUCTION SERVER STARTUP] {app_name}\n"
                f"   • Started At : {_server_boot_time.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"   • Environment: {env}\n"
                f"   • Process ID : {pid}\n"
                f"   • Platform   : {platform.system()} {platform.release()} ({platform.machine()})\n"
                f"   • Python     : {platform.python_version()}\n"
                f"   • Log File   : {settings.LOG_DIR}/{settings.SERVER_LOG_FILE}\n"
                f"   • Crash File : {settings.LOG_DIR}/{settings.CRASH_LOG_FILE}\n"
                + "=" * 80
            )
        logger.info(banner)

    @classmethod
    def record_shutdown(
        cls,
        reason: str = "Graceful Lifespan Shutdown",
        exc: Exception | None = None,
        exit_code: int = 0,
    ) -> None:
        global _shutdown_recorded
        if _shutdown_recorded:
            return
        _shutdown_recorded = True

        uptime_s = cls.get_uptime_seconds()
        uptime_h = cls.get_uptime_human()
        now_utc = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        pid = os.getpid()

        # SIGINT (130) and SIGTERM (143) are standard graceful stops (developer Ctrl+C or container orchestrator teardown)
        is_user_interrupt = (exit_code == 130) or ("SIGINT" in reason) or ("KeyboardInterrupt" in reason)
        is_container_stop = (exit_code == 143) or ("SIGTERM" in reason)
        is_abnormal = (exit_code not in (0, 130, 143)) or (exc is not None)

        if settings.is_development:
            if is_user_interrupt:
                # In development, Ctrl+C is the expected, routine developer stop
                msg = (
                    "\n" + "-" * 70 + "\n"
                    f"👋 [DEV SERVER STOPPED] Stopped via Ctrl+C\n"
                    f"   • Uptime     : {uptime_s}s ({uptime_h})\n"
                    f"   • Process ID : {pid}\n"
                    + "-" * 70
                )
                logger.info(msg)
                return

            if not is_abnormal:
                msg = (
                    "\n" + "-" * 70 + "\n"
                    f"ℹ️ [DEV SERVER SHUTDOWN] {reason}\n"
                    f"   • Uptime     : {uptime_s}s ({uptime_h})\n"
                    + "-" * 70
                )
                logger.info(msg)
                return

            # Dev mode abnormal crash
            status_label = "🚨 [DEV SERVER ABNORMAL CRASH]"
        else:
            # Production mode
            if is_container_stop or is_user_interrupt:
                msg = (
                    "\n" + "=" * 80 + "\n"
                    f"⚠️ [CONTAINER TERMINATION] Container Received Termination Signal\n"
                    f"   • Timestamp  : {now_utc}\n"
                    f"   • Reason     : {reason}\n"
                    f"   • Uptime     : {uptime_s}s ({uptime_h})\n"
                    f"   • Process ID : {pid}\n"
                    f"   • Exit Code  : {exit_code}\n"
                    + "=" * 80
                )
                logger.warning(msg)
                return

            status_label = "⚠️ [PRODUCTION SERVER SHUTDOWN]" if not is_abnormal else "🚨 [PRODUCTION SERVER CRASH / OUTAGE]"

        details = [
            "\n" + "=" * 80,
            f"{status_label}",
            f"   • Timestamp  : {now_utc}",
            f"   • Reason     : {reason}",
            f"   • Uptime     : {uptime_s}s ({uptime_h})",
            f"   • Process ID : {pid}",
            f"   • Exit Code  : {exit_code}",
        ]

        if exc:
            details.append(f"   • Exception  : {type(exc).__name__}: {exc}")
            details.append("   • Traceback  :")
            details.append("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

        details.append("=" * 80)
        message = "\n".join(details)

        if is_abnormal:
            crash_logger.critical(message)
        else:
            logger.info(message)

        # Flush all handlers immediately so logs are persisted before process exits
        for handler in logging.root.handlers:
            try:
                handler.flush()
            except (ValueError, OSError):
                continue
        for handler in crash_logger.handlers:
            try:
                handler.flush()
            except (ValueError, OSError):
                continue

    @classmethod
    def record_crash(
        cls,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: Any = None,
        origin: str = "Unhandled Exception",
    ) -> None:
        """Record fatal crash / unhandled exception causing server termination."""
        uptime_s = cls.get_uptime_seconds()
        uptime_h = cls.get_uptime_human()
        now_utc = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        pid = os.getpid()

        type_name = exc_type.__name__ if exc_type is not None else "UnknownException"
        val_str = str(exc_val) if exc_val is not None else "No error details available"

        if exc_type is not None and exc_val is not None:
            tb_lines = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
        elif exc_tb is not None:
            tb_lines = "".join(traceback.format_tb(exc_tb))
        else:
            tb_lines = "No traceback available"

        env_label = "PRODUCTION" if settings.is_production else "DEVELOPMENT"
        crash_banner = (
            "\n" + "!" * 80 + "\n"
            f"🚨 [{env_label} SERVER FATAL CRASH DETECTED - SERVER GETTING DOWN]\n"
            f"   • Timestamp      : {now_utc}\n"
            f"   • Mode           : {settings.APP_ENV.upper()}\n"
            f"   • Origin         : {origin}\n"
            f"   • Process ID     : {pid}\n"
            f"   • Uptime At Crash: {uptime_s}s ({uptime_h})\n"
            f"   • Exception Type : {type_name}\n"
            f"   • Error Details  : {val_str}\n"
            f"   • Stack Trace    :\n{tb_lines}"
            + "!" * 80
        )
        crash_logger.critical(crash_banner)

        # Flush immediately
        for handler in logging.root.handlers:
            try:
                handler.flush()
            except (ValueError, OSError):
                continue
        for handler in crash_logger.handlers:
            try:
                handler.flush()
            except (ValueError, OSError):
                continue


def _sys_excepthook(
    exc_type: type[BaseException],
    exc_val: BaseException,
    exc_tb: TracebackType | None,
) -> None:
    """Global sys.excepthook to log any fatal unhandled exceptions before Python interpreter exits."""
    if issubclass(exc_type, KeyboardInterrupt):
        # Normal Ctrl+C interrupt
        ServerLifecycleTracker.record_shutdown(reason="KeyboardInterrupt (SIGINT / Ctrl+C)", exit_code=130)
        sys.__excepthook__(exc_type, exc_val, exc_tb)
        return

    ServerLifecycleTracker.record_crash(
        exc_type=exc_type,
        exc_val=exc_val,
        exc_tb=exc_tb,
        origin="sys.excepthook (Main Thread Uncaught Exception)",
    )
    sys.__excepthook__(exc_type, exc_val, exc_tb)


def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """Global threading.excepthook to catch unhandled worker thread exceptions."""
    thread_name = args.thread.name if args.thread is not None else "UnknownThread"
    ServerLifecycleTracker.record_crash(
        exc_type=args.exc_type,
        exc_val=args.exc_value,
        exc_tb=args.exc_traceback,
        origin=f"threading.excepthook (Thread: {thread_name})",
    )


def _atexit_handler() -> None:
    """Called automatically when the Python interpreter exits."""
    if sys.is_finalizing():
        return
    try:
        ServerLifecycleTracker.record_shutdown(reason="Interpreter atexit termination")
    except (ValueError, OSError):
        pass


def _setup_signal_logging() -> None:
    """Register signal handlers to log termination events (SIGTERM, SIGINT)."""
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)

    def handle_sigterm(signum: int, frame: Any) -> None:
        ServerLifecycleTracker.record_shutdown(
            reason="Received SIGTERM (OS/Container Termination Signal)",
            exit_code=143,
        )
        if callable(original_sigterm):
            original_sigterm(signum, frame)
        else:
            sys.exit(143)

    def handle_sigint(signum: int, frame: Any) -> None:
        ServerLifecycleTracker.record_shutdown(
            reason="Received SIGINT (Keyboard Interrupt / Console Stop)",
            exit_code=130,
        )
        if callable(original_sigint):
            original_sigint(signum, frame)
        else:
            sys.exit(130)

    try:
        signal.signal(signal.SIGTERM, handle_sigterm)
    except (ValueError, OSError) as exc:
        logger.debug(f"Unable to register SIGTERM handler: {exc}")

    try:
        signal.signal(signal.SIGINT, handle_sigint)
    except (ValueError, OSError) as exc:
        logger.debug(f"Unable to register SIGINT handler: {exc}")

    # Register SIGHUP on Unix if available
    if hasattr(signal, "SIGHUP"):
        def handle_sighup(signum: int, frame: Any) -> None:
            ServerLifecycleTracker.record_shutdown(
                reason="Received SIGHUP (Hangup Signal)",
                exit_code=129,
            )
        try:
            signal.signal(signal.SIGHUP, handle_sighup)
        except (ValueError, OSError):
            pass


def setup_server_logging() -> None:
    """
    Configure enterprise-grade structured logging tailored for Dev vs. Prod:
    - Dev Mode: High readability, streamlined console, silenced background connection pool spam.
    - Prod Mode: Machine-parseable logs, container lifecycle auditing, dedicated crash file recording.
    """
    global _lifecycle_initialized
    if _lifecycle_initialized:
        return
    _lifecycle_initialized = True

    # 1. Environment-aware log format
    if settings.is_development:
        log_format = "%(asctime)s [%(levelname)s] [%(name)s]: %(message)s"
        log_level = logging.DEBUG if settings.DEBUG else logging.INFO
    else:
        log_format = "%(asctime)s [%(process)d:%(threadName)s] [%(levelname)s] [%(name)s]: %(message)s"
        log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    formatter = logging.Formatter(fmt=log_format, datefmt="%Y-%m-%d %H:%M:%S")

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 2. Console Handler configuration
    has_console = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root_logger.handlers)
    if not has_console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(log_level)
        root_logger.addHandler(console_handler)
    else:
        for h in root_logger.handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
                h.setFormatter(formatter)

    # 3. Silence chatty third-party connection pool loggers so PostHog/OTel batch flushes don't drown out app logs
    noisy_loggers = [
        "urllib3",
        "urllib3.connectionpool",
        "requests",
        "httpcore",
        "httpx",
        "opentelemetry",
        "asyncio",
        "watchfiles",
    ]
    for noisy in noisy_loggers:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 4. File Handlers
    if settings.ENABLE_FILE_LOGGING:
        backend_dir = Path(__file__).resolve().parent.parent.parent
        log_dir = backend_dir / settings.LOG_DIR
        try:
            log_dir.mkdir(parents=True, exist_ok=True)

            server_log_path = log_dir / settings.SERVER_LOG_FILE
            crash_log_path = log_dir / settings.CRASH_LOG_FILE

            # Rotating file handler (server.log)
            server_file_handler = RotatingFileHandler(
                filename=str(server_log_path),
                maxBytes=settings.MAX_LOG_BYTES,
                backupCount=settings.BACKUP_LOG_COUNT,
                encoding="utf-8",
            )
            server_file_handler.setFormatter(formatter)
            server_file_handler.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
            root_logger.addHandler(server_file_handler)

            # Crash-specific rotating file handler (server_crash.log)
            crash_file_handler = RotatingFileHandler(
                filename=str(crash_log_path),
                maxBytes=settings.MAX_LOG_BYTES,
                backupCount=settings.BACKUP_LOG_COUNT,
                encoding="utf-8",
            )
            crash_file_handler.setFormatter(formatter)
            crash_file_handler.setLevel(logging.WARNING)

            crash_logger.addHandler(crash_file_handler)
            crash_logger.setLevel(logging.WARNING)
            crash_logger.propagate = True

        except (OSError, ValueError) as exc:
            print(f"Warning: Failed to initialize file loggers in {log_dir}: {exc}", file=sys.stderr)

    # 5. Register crash and exit hooks
    sys.excepthook = _sys_excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _threading_excepthook
    atexit.register(_atexit_handler)
    _setup_signal_logging()

    # 6. Log initial boot
    ServerLifecycleTracker.record_startup()
