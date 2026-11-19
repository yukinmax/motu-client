import json
import logging

from aioprometheus import MetricsMiddleware
from aioprometheus.asgi.quart import metrics
from quart import Quart, request

import binding
import mappings
import motu
import raw_panel
from settings import settings

logging.basicConfig(
    level=settings.log_level,
    format="%(levelname)s [%(name)s] %(message)s",
)

logger = logging.getLogger(__name__)
if settings.log_level != "INFO":
    logger.log(
        logging.getLevelNamesMapping()[settings.log_level],
        "Log level set to %s",
        settings.log_level,
    )

app = Quart("MOTU API")
app.config["BACKGROUND_TASK_SHUTDOWN_TIMEOUT"] = settings.shutdown_timeout
motu_http_client = motu.HTTPClient(
    retry_interval=settings.motu_retry_interval,
    connector_limit=settings.motu_connector_limit,
)
motu_ds = motu.DataStore(
    motu_http_client,
    host=settings.motu_host,
    port=settings.motu_port,
    refresh_timeout=settings.motu_refresh_timeout,
    refresh_retries=settings.motu_refresh_retries,
    mutate_timeout=settings.motu_mutate_timeout,
    mutate_retries=settings.motu_mutate_retries,
)
motu_ms = motu.Meters(
    motu_http_client,
    host=settings.motu_host,
    port=settings.motu_port,
    refresh_timeout=settings.motu_refresh_timeout,
    refresh_retries=settings.motu_refresh_retries,
)
skaarhoj_panel = raw_panel.RawPanel(
    host=settings.panel_host,
    port=settings.panel_port,
    delay=settings.panel_delay,
    idle_sleep=settings.panel_idle_sleep,
    sleep_timeout=settings.panel_sleep_timeout,
    sleep_mode=settings.panel_sleep_mode,
    sleep_screen_saver=settings.panel_sleep_screen_saver,
    panel_brightness_leds=settings.panel_brightness_leds,
    panel_brightness_oleds=settings.panel_brightness_oleds,
    panel_dimmed_gain=settings.panel_dimmed_gain,
    connect_retry_interval=settings.panel_connect_retry_interval,
    connect_timeout=settings.panel_connect_timeout,
    send_timeout=settings.panel_send_timeout,
    wakeup_min_interval=settings.panel_wakeup_min_interval,
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
    await skaarhoj_panel.connect(retries=settings.panel_connect_retries)
    app.add_background_task(skaarhoj_panel.handle_requests)
    app.add_background_task(skaarhoj_panel.process_buffers)
    app.add_background_task(motu_ds.poll, diff_check=False)
    app.add_background_task(motu_ms.poll, idle_interval=settings.meters_idle_interval)


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
    try:
        bus = str(request.args["bus"])
    except KeyError:
        return "Error: No bus field provided. Please specify bus."
    try:
        channel = int(request.args["index"])
    except KeyError:
        return "Error: No index field provided. Please specify index."
    path = f"mix/{bus}/{channel}/matrix/mute"
    result = await motu_ds.toggle(path)
    if result is None:
        return json.dumps({"status": "FAILURE"})
    return json.dumps({"status": str(result)})


@app.route("/api/v1/motu/mute-status", methods=["GET"])
async def mute_status() -> str:
    try:
        bus = str(request.args["bus"])
    except KeyError:
        return "Error: No bus field provided. Please specify bus."
    try:
        channel = int(request.args["index"])
    except KeyError:
        return "Error: No index field provided. Please specify index."
    path = f"mix/{bus}/{channel}/matrix/mute"
    result = motu_ds.get(path)
    if result is None:
        return json.dumps({"status": "FAILURE"})
    return json.dumps({"status": str(result)})


@app.route("/api/v1/motu/aux-send-level", methods=["GET"])
async def mix_send_level_get() -> str:
    try:
        channel = int(request.args["chan"])
    except KeyError:
        return "Error: No chan field provided. Please specify channel #."
    try:
        aux = int(request.args["aux"])
    except KeyError:
        return "Error: No aux field provided. Please specify aux #."
    path = f"mix/chan/{channel}/matrix/aux/{aux}/send"
    result = motu_ds.get(path)
    if result is None:
        return json.dumps({"status": "FAILURE"})
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
    try:
        channel = int(request.args["chan"])
    except KeyError:
        return "Error: No chan field provided. Please specify channel #."
    try:
        aux = int(request.args["aux"])
    except KeyError:
        return "Error: No aux field provided. Please specify aux #."
    try:
        value = float(request.args["value"])
    except KeyError:
        return "Error: No value field provided. Please specify new value."
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
    result = await motu_ds.set(path, value)
    if result is None:
        return json.dumps({"status": "FAILURE"})
    return json.dumps({"status": f"{result:.10f}"})


if __name__ == "__main__":
    app.run(host=settings.api_host, port=settings.api_port)
