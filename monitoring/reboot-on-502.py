#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "python-dotenv",
#     "requests",
#     "rich",
# ]
# ///

"""
Script to check whether bad gateway error has occurred 
"""

#!/usr/bin/env python3

import requests
import subprocess
import time
import os
import sys
import logging
import signal
import atexit
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import List, Optional
from collections import deque
from rich.logging import RichHandler
from rich. console import Console
from rich.panel import Panel
from rich.table import Table

# Detect if running in a TTY (interactive terminal) or as a service
IS_TTY = sys.stdout.isatty()

# Setup Rich console with appropriate settings
console = Console(
    force_terminal=IS_TTY,
    force_interactive=IS_TTY,
    force_jupyter=False
)

# Configure logging
if IS_TTY:
    handler = RichHandler(
        rich_tracebacks=True,
        console=console,
        show_path=False,
        markup=True
    )
    log_format = "%(message)s"
else:
    handler = RichHandler(
        rich_tracebacks=False,
        console=console,
        show_path=True,
        markup=False,
        show_time=True
    )
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt="[%Y-%m-%d %H:%M:%S]",
    handlers=[handler]
)

logger = logging.getLogger("http_monitor")

# Global shutdown flag
shutdown_requested = False

# Pause file location
# PAUSE_FILE = Path("/var/run/http-monitor.pause")


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully"""
    global shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    shutdown_requested = True


# Register signal handlers
signal.signal(signal.SIGTERM, signal_handler)  # systemctl stop
signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C


@dataclass
class MonitorConfig: 
    """Configuration for the HTTP monitor"""
    url: str
    check_interval: int
    telegram_bot_token: str
    telegram_chat_id: str
    recovery_commands: List[str]
    pause_file: Path
    log_level: str = "INFO"
    maintenance_windows: List[tuple] = None
    error_threshold: int = 3
    error_window_seconds: int = 180
    
    @classmethod
    def from_environment(cls) -> 'MonitorConfig':
        """Load configuration from environment variables"""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError: 
            logger. warning("python-dotenv not installed.  Using environment variables only.")
        
        url = os.getenv("MONITOR_URL", "http://localhost:80")
        check_interval = int(os.getenv("CHECK_INTERVAL", "30"))
        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os. getenv("TELEGRAM_CHAT_ID")
        log_level = os.getenv("LOG_LEVEL", "INFO")
        error_threshold = int(os.getenv("ERROR_THRESHOLD", "3"))
        error_window_seconds = int(os.getenv("ERROR_WINDOW_SECONDS", "180"))
        pause_file = Path(os.getenv("PAUSE_FILE", "/var/run/http-monitor.pause"))
        
        commands_str = os.getenv("RECOVERY_COMMANDS", "systemctl status nginx,echo 'Command 2'")
        recovery_commands = [cmd.strip() for cmd in commands_str.split(",")]
        
        # Parse maintenance windows
        maintenance_windows = cls._parse_maintenance_windows(
            os.getenv("MAINTENANCE_WINDOWS", "")
        )
        
        return cls(
            url=url,
            check_interval=check_interval,
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
            recovery_commands=recovery_commands,
            pause_file=pause_file,
            log_level=log_level,
            maintenance_windows=maintenance_windows,
            error_threshold=error_threshold,
            error_window_seconds=error_window_seconds
        )
    
    @staticmethod
    def _parse_maintenance_windows(windows_str: str) -> List[tuple]:
        """Parse maintenance windows from string like '03:00-03:10,15:00-15:05'"""
        if not windows_str:
            return []
        
        windows = []
        for window in windows_str.split(","):
            window = window.strip()
            if not window:
                continue
            
            try:
                start_str, end_str = window.split("-")
                start_hour, start_min = map(int, start_str.split(":"))
                end_hour, end_min = map(int, end_str.split(":"))
                
                windows.append((
                    dt_time(start_hour, start_min),
                    dt_time(end_hour, end_min)
                ))
            except ValueError as _:
                logger.error(f"Invalid maintenance window format: {window}")
        
        return windows
    
    def validate(self) -> None:
        """Validate that required configuration is present"""
        if not self. telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN not set in environment")
        if not self.telegram_chat_id:
            raise ValueError("TELEGRAM_CHAT_ID not set in environment")
        
        if IS_TTY:
            # Create a nice configuration table for interactive use
            table = Table(title="Configuration", show_header=False, border_style="green")
            table.add_column("Setting", style="cyan")
            table.add_column("Value", style="yellow")
            
            table.add_row("Monitoring URL", self.url)
            table.add_row("Check Interval", f"{self.check_interval}s")
            table.add_row("Chat ID", self.telegram_chat_id)
            table.add_row("Commands", str(len(self.recovery_commands)))
            table.add_row("Log Level", self.log_level)
            table.add_row("Error Threshold", f"{self.error_threshold} in {self.error_window_seconds}s")
            
            if self.maintenance_windows:
                windows_str = ", ".join([f"{s. strftime('%H:%M')}-{e.strftime('%H:%M')}" 
                                        for s, e in self.maintenance_windows])
                table.add_row("Maintenance Windows", windows_str)
            
            table.add_row("Pause File", str(self.pause_file))
            table.add_row("Running as", "Interactive TTY" if IS_TTY else "Service/Background")
            
            console.print(table)
        else:
            # Simple log output for services
            logger.info(
                f"Configuration:  URL={self.url}, Interval={self.check_interval}s, "
                f"ChatID={self.telegram_chat_id}, Commands={len(self.recovery_commands)}, "
                f"ErrorThreshold={self.error_threshold}/{self.error_window_seconds}s"
            )
        
        logger.info("Configuration validated successfully")


class ErrorTracker:
    """Track errors over time to prevent false positives"""
    
    def __init__(self, threshold: int, window_seconds: int):
        self.threshold = threshold
        self.window = timedelta(seconds=window_seconds)
        self.errors = deque()
    
    def add_error(self) -> bool:
        """Add an error and return True if threshold exceeded"""
        now = datetime.now()
        self.errors.append(now)
        
        # Remove errors outside the time window
        cutoff = now - self.window
        while self.errors and self.errors[0] < cutoff: 
            self.errors.popleft()
        
        return len(self.errors) >= self.threshold
    
    def clear(self):
        """Clear all errors"""
        self.errors.clear()
    
    def get_count(self) -> int:
        """Get current error count in window"""
        return len(self. errors)


def is_in_maintenance_window(config: MonitorConfig) -> bool:
    """Check if current time is within a maintenance window"""
    if not config.maintenance_windows:
        return False
    
    now = datetime.now().time()
    
    for start_time, end_time in config. maintenance_windows:
        # Handle windows that cross midnight
        if start_time <= end_time:
            if start_time <= now <= end_time:
                return True
        else:  # Window crosses midnight (e.g., 23:00-01:00)
            if now >= start_time or now <= end_time:
                return True
    
    return False


def is_monitoring_paused(pause_file: Path) -> bool:
    """Check if monitoring is paused via pause file"""
    return pause_file.exists()


def send_telegram_message(config: MonitorConfig, message: str) -> None:
    """Send a message via Telegram bot"""
    try:
        url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": config.telegram_chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info("Telegram notification sent successfully")
        else:
            logger.error(f"Failed to send Telegram notification: {response.text}")
            
    except Exception as e: 
        logger.exception(f"Error sending Telegram message: {e}")


def check_url(url: str) -> Optional[int]:
    """Check the HTTP status code of the URL"""
    try:
        response = requests.get(url, timeout=5)
        return response.status_code
    except requests.exceptions. Timeout:
        logger.warning(f"Timeout connecting to {url}")
        return None
    except requests.exceptions.ConnectionError:
        logger.error(f"Connection error to {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger. error(f"Request error:  {e}")
        return None


def run_commands(commands: List[str]) -> List[str]:
    """Execute the configured commands and return outputs"""
    logger.warning("Running recovery commands...")
    
    command_outputs = []
    
    for cmd in commands:
        cmd = cmd.strip()
        try:
            if IS_TTY:
                logger.info(f"Executing:  [cyan]{cmd}[/cyan]", extra={"markup": True})
            else:
                logger.info(f"Executing: {cmd}")
            
            result = subprocess. run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.stdout:
                if IS_TTY:
                    console.print(Panel(
                        result.stdout[: 500],
                        title=f"Output:  {cmd[: 50]}",
                        border_style="blue"
                    ))
                else:
                    logger.info(f"Command output: {result.stdout[: 200]}")
            
            # Store output for Telegram message
            output_preview = result.stdout[:200] if result.stdout else "(no output)"
            command_outputs.append(f"<code>{cmd}</code>\n{output_preview}")
            
            if result.stderr:
                logger.error(f"Command stderr: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out: {cmd}")
            command_outputs.append(f"<code>{cmd}</code>\nFailed:  Timeout")
        except Exception as e: 
            logger.exception(f"Failed to run command '{cmd}'")
            command_outputs.append(f"<code>{cmd}</code>\nFailed: {e}")
    
    logger.info("Commands execution completed")
    return command_outputs


def get_hostname() -> str:
    """Get the system hostname"""
    return os.uname().nodename if hasattr(os, 'uname') else 'Unknown'


def handle_502_error(config: MonitorConfig, error_count: int, error_tracker: ErrorTracker) -> None:
    """Handle a 502 error by running commands and sending notification"""
    logger.critical(
        f"502 Bad Gateway - threshold exceeded!  "
        f"({error_tracker.get_count()} errors detected)"
    )
    
    # Run recovery commands
    command_outputs = run_commands(config.recovery_commands)
    
    # Prepare Telegram message
    hostname = get_hostname()
    message = (
        f"🔴 <b>502 Bad Gateway Detected! </b>\n\n"
        f"URL: <code>{config.url}</code>\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Host: {hostname}\n"
        f"Alert Count: {error_count}\n"
        f"Errors in window: {error_tracker.get_count()}\n\n"
        f"<b>Commands executed:</b>\n"
    )
    
    for output in command_outputs:
        message += f"\n{output}\n"
    
    # Send notification
    send_telegram_message(config, message)


def log_status(status_code: Optional[int]) -> None:
    """Log the HTTP status code"""
    if status_code == 200:
        if IS_TTY:
            logger.info(f"✓ Status:  [green]{status_code}[/green] (OK)", extra={"markup": True})
        else:
            logger.info(f"Status: {status_code} (OK)")
    elif status_code: 
        if IS_TTY:
            logger.info(f"Status: [yellow]{status_code}[/yellow]", extra={"markup": True})
        else:
            logger. info(f"Status: {status_code}")


def print_banner() -> None:
    """Print startup banner"""
    if IS_TTY:
        console.print(Panel. fit(
            "[bold cyan]HTTP Monitor with Telegram Alerts[/bold cyan]\n"
            "[dim]Monitoring for 502 errors[/dim]",
            border_style="cyan"
        ))
    else:
        logger.info("HTTP Monitor with Telegram Alerts - Starting")


def send_startup_notification(config: MonitorConfig) -> None:
    """Send a Telegram notification that monitoring has started"""
    hostname = get_hostname()
    
    features = []
    if config.maintenance_windows:
        features.append(f"Maintenance windows: {len(config.maintenance_windows)}")
    features.append(f"Error threshold: {config.error_threshold}/{config.error_window_seconds}s")
    
    send_telegram_message(
        config,
        f"🟢 <b>Monitoring Started</b>\n\n"
        f"URL: <code>{config.url}</code>\n"
        f"Interval: {config.check_interval}s\n"
        f"Host: {hostname}\n"
        f"Mode: {'Interactive' if IS_TTY else 'Service'}\n\n"
        f"<b>Features:</b>\n" + "\n".join([f"• {f}" for f in features])
    )


def send_shutdown_notification(config: MonitorConfig, error_count: int) -> None:
    """Send a Telegram notification that monitoring has stopped"""
    send_telegram_message(
        config,
        f"🟡 <b>Monitoring Stopped</b>\n\n"
        f"URL: <code>{config.url}</code>\n"
        f"Total alerts sent: {error_count}"
    )


def run_monitor(config: MonitorConfig) -> None:
    """Main monitoring loop"""
    logger.info(f"Starting monitoring on {config.url} (interval: {config.check_interval}s)")
    logger.info(f"Alert threshold: {config.error_threshold} errors in {config.error_window_seconds}s")
    
    if config.maintenance_windows:
        logger.info(f"Maintenance windows configured: {len(config.maintenance_windows)}")
    
    logger.info(f"Pause file: {config.pause_file}")
    
    if IS_TTY:
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")
    
    send_startup_notification(config)
    
    alert_count: int = 0
    error_tracker = ErrorTracker(config.error_threshold, config.error_window_seconds)
    in_maintenance: bool = False
    was_paused: bool = False
    
    # Register cleanup
    atexit.register(lambda: send_shutdown_notification(config, alert_count))
    
    try:
        while not shutdown_requested:
            # Check pause file
            if is_monitoring_paused(config.pause_file):
                if not was_paused:
                    logger.info("⏸️  Monitoring paused (pause file detected)")
                    was_paused = True
                
                time.sleep(config.check_interval)
                continue
            else:
                if was_paused:
                    logger.info("▶️  Monitoring resumed (pause file removed)")
                    was_paused = False
            
            # Check maintenance window
            if is_in_maintenance_window(config):
                if not in_maintenance:
                    logger.info("⏸️  Entered maintenance window - monitoring paused")
                    in_maintenance = True
                
                time.sleep(config.check_interval)
                continue
            else: 
                if in_maintenance:
                    logger.info("▶️  Exited maintenance window - monitoring resumed")
                    in_maintenance = False
            
            # Perform health check
            status_code = check_url(config.url)
            
            if status_code == 502:
                should_alert = error_tracker.add_error()
                
                if should_alert:
                    alert_count += 1
                    handle_502_error(config, alert_count, error_tracker)
                    error_tracker.clear()  # Reset after handling
                else:
                    logger.warning(
                        f"502 Bad Gateway detected, but below threshold "
                        f"({error_tracker.get_count()}/{config.error_threshold})"
                    )
            else:
                # Clear errors on successful response
                if error_tracker.get_count() > 0:
                    logger.info(
                        f"Service recovered, clearing error count "
                        f"(was {error_tracker.get_count()})"
                    )
                    error_tracker.clear()
                
                log_status(status_code)
            
            # Sleep in smaller intervals to check shutdown flag
            for _ in range(config.check_interval):
                if shutdown_requested:
                    break
                time.sleep(1)
            
    except KeyboardInterrupt:
        if IS_TTY:
            console.print("\n")
        logger.info("Monitoring stopped by user")
    finally:
        # Cleanup is handled by atexit
        pass


def main() -> None:
    """Main entry point"""
    print_banner()
    
    # Load and validate configuration
    try:
        config = MonitorConfig.from_environment()
        
        # Set log level
        logger.setLevel(getattr(logging, config.log_level.upper()))
        
        config.validate()
    except ValueError as e:
        logger.critical(f"Configuration error: {e}")
        if IS_TTY:
            console.print("\n[red]Please create a . env file or set environment variables.[/red]")
        else:
            logger.critical("Please set required environment variables:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID")
        return
    
    # Run the monitor
    run_monitor(config)


if __name__ == "__main__":
    main()