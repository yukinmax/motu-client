import json
import logging
import os

from aioprometheus import MetricsMiddleware
from aioprometheus.asgi.quart import metrics
from quart import Quart, request

import binding
import mappings
import motu
import raw_panel

_VALID_LOG_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

log_level_name = os.getenv("LOG_LEVEL", "INFO").strip().upper()
if log_level_name in _VALID_LOG_LEVELS:
    log_level = _VALID_LOG_LEVELS[log_level_name]
else:
    log_level = logging.INFO

logging.basicConfig(
    level=log_level,
    format="%(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)

if log_level_name not in _VALID_LOG_LEVELS:
    logger.warning("Invalid LOG_LEVEL=%r; falling back to INFO", log_level_name)
elif log_level_name != "INFO":
    logger.log(log_level, "Log level set to %s", log_level_name)

app = Quart("MOTU API")
app.config["BACKGROUND_TASK_SHUTDOWN_TIMEOUT"] = 5
motu_http_client = motu.HTTPClient()
motu_ds = motu.DataStore(motu_http_client, "ultralite-avb.ynet")
motu_ms = motu.Meters(motu_http_client, "ultralite-avb.ynet")
skaarhoj_panel = raw_panel.RawPanel(
    "waveboard.ynet",
    delay=0.001,
    sleep_timeout_minutes=10,
)

control_map = binding.ControlMap.from_raw(mappings.control_map)
feedback_map = binding.FeedbackMap.from_raw(mappings.feedback_map)

motu_controller = binding.MotuController(
    control_map,
    range_mapping=binding.FADERS_RANGE_MAPPING,
)
panel_controller = binding.PanelController(
    feedback_map,
    faders_range_mapping=binding.FADERS_RANGE_MAPPING,
    meters_range_mapping=binding.METERS_RANGE_MAPPING,
)

motu_controller.set_datastore(motu_ds)
skaarhoj_panel.set_change_controller(motu_controller)

panel_controller.set_panel(skaarhoj_panel)
panel_controller.set_datastore(motu_ds)
panel_controller.set_meters(motu_ms)
skaarhoj_panel.set_feedback_controller(panel_controller)


motu_ds.set_change_controller(panel_controller)
motu_ms.set_change_controller(panel_controller)

app.asgi_app = MetricsMiddleware(app.asgi_app)
app.add_url_rule("/metrics", "metrics", metrics, methods=["GET"])


@app.before_serving
async def startup() -> None:
    await motu_http_client.start()
    await motu_ds.refresh(handle_changes=False)
    await motu_ms.refresh(handle_changes=False)
    logger.info("Initial data refresh has completed")
    await skaarhoj_panel.connect()
    app.add_background_task(skaarhoj_panel.handle_requests)
    app.add_background_task(skaarhoj_panel.process_buffers)
    app.add_background_task(motu_ds.poll, diff_check=False)
    app.add_background_task(motu_ms.poll, idle_interval=0.2)


@app.after_serving
async def shutdown() -> None:
    logger.info("Shutting down...")
    skaarhoj_panel.stop()
    motu_ds.stop()
    motu_ms.stop()
    await skaarhoj_panel.disconnect()
    await motu_http_client.close()
    logger.info("Shutdown complete")


@app.route("/", methods=["GET"])
async def home() -> str:
    return "<h1>MOTU API</h1>"


@app.route("/api/v1/panel", methods=["POST"])
async def panel_command() -> str:
    try:
        command = request.args["command"]
    except KeyError:
        return "Error: No command provided."
    await skaarhoj_panel.send(command)
    return "OK"


@app.route("/api/v1/motu/mute-toggle", methods=["GET"])
async def mute_toggle() -> str:
    if "bus" not in request.args:
        return "Error: No bus field provided. Please specify bus."
    if "index" not in request.args:
        return "Error: No index field provided. Please specify index."
    bus = str(request.args["bus"])
    channel = int(request.args["index"])
    path = f"mix/{bus}/{channel}/matrix/mute"
    value = await motu_ds.toggle(path)
    if value is None:
        return json.dumps({"status": "FAILURE"})
    return json.dumps({"status": str(value)})


@app.route("/api/v1/motu/mute-status", methods=["GET"])
async def mute_status() -> str:
    if "bus" not in request.args:
        return "Error: No bus field provided. Please specify bus."
    if "index" not in request.args:
        return "Error: No index field provided. Please specify index."
    bus = str(request.args["bus"])
    channel = int(request.args["index"])
    path = f"mix/{bus}/{channel}/matrix/mute"
    return json.dumps({"status": str(motu_ds.get(path))})


@app.route("/api/v1/motu/aux-send-level", methods=["GET"])
async def mix_send_level_get() -> str:
    if "chan" not in request.args:
        return "Error: No channel field provided. Please specify channel #."
    if "aux" not in request.args:
        return "Error: No aux field provided. Please specify aux #."
    channel = int(request.args["chan"])
    aux = int(request.args["aux"])
    path = f"mix/chan/{channel}/matrix/aux/{aux}/send"
    result = motu_ds.get(path)
    try:
        fmt = request.args["format"]
    except KeyError:
        pass
    else:
        if fmt in ("db", "raw"):
            result = motu.level_to_db(result)
        if fmt == "raw":
            result = binding.db_to_raw(result, binding.FADERS_RANGE_MAPPING)
    return json.dumps({"status": f"{result:.10f}"})


@app.route("/api/v1/motu/aux-send-level", methods=["POST", "PATCH"])
async def mix_send_level_set() -> str:
    if "chan" not in request.args:
        return "Error: No channel field provided. Please specify channel #."
    if "aux" not in request.args:
        return "Error: No aux field provided. Please specify aux #."
    if "value" not in request.args:
        return "Error: No value field provided. Please specify new value."
    channel = int(request.args["chan"])
    aux = int(request.args["aux"])
    value = float(request.args["value"])
    try:
        fmt = request.args["format"]
    except KeyError:
        value = motu.limit_to_range(
            value,
            motu.LEVEL_RANGE[0],
            motu.LEVEL_RANGE[1],
        )
    else:
        if fmt == "raw":
            value = binding.raw_to_db(value, binding.FADERS_RANGE_MAPPING)
        if fmt in ("db", "raw"):
            value = motu.db_to_level(value)
    path = f"mix/chan/{channel}/matrix/aux/{aux}/send"
    await motu_ds.set(path, value)
    result = motu_ds.get(path)
    return json.dumps({"status": f"{result:.10f}"})


if __name__ == "__main__":
    app.run(port="5088")
