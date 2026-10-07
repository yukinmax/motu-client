from quart import Quart, request
import json
import logging
import os
from aioprometheus import MetricsMiddleware
from aioprometheus.asgi.quart import metrics
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
    logger.warning(
        "Invalid LOG_LEVEL=%r; falling back to INFO",
        log_level_name
    )

app = Quart('MOTU API')
app.config["DEBUG"] = True
motu_http_client = motu.HTTPClient()
motu_ds = motu.DataStore(motu_http_client, "ultralite-avb.ynet")
motu_ms = motu.Meters(motu_http_client, "ultralite-avb.ynet")
skaarhoj_panel = raw_panel.RawPanel(
    'waveboard.ynet',
    delay=0.001,
    sleep_timeout=600,
)
skaarhoj_panel.set_ds(motu_ds)
skaarhoj_panel.set_ms(motu_ms)
motu_ds.set_change_handler(skaarhoj_panel.process_data_feedback)
motu_ms.set_change_handler(skaarhoj_panel.process_meters_feedback)

app.asgi_app = MetricsMiddleware(app.asgi_app)
app.add_url_rule('/metrics', 'metrics', metrics, methods=['GET'])

raw_db_range_mapping = raw_panel.raw_db_range_mapping


@app.before_serving
async def startup():
    await motu_http_client.start()
    app.add_background_task(skaarhoj_panel.handle_requests)
    app.add_background_task(skaarhoj_panel.process_buffers)
    app.add_background_task(skaarhoj_panel.handle_sleep_timeout)
    await skaarhoj_panel.connect()
    await motu_ds.refresh()
    await motu_ms.refresh()
    logger.info("Initial data refresh has completed")
    app.add_background_task(motu_ds.poll, diff_check=False)
    app.add_background_task(motu_ms.poll)
    # app.add_background_task(skaarhoj_panel.handle_requests)
    # app.add_background_task(skaarhoj_panel.process_buffers)


@app.after_serving
async def shutdown():
    await motu_http_client.close()


@app.route('/', methods=['GET'])
async def home():
    return '<h1>MOTU API</h1>'


@app.route('/api/v1/panel', methods=['POST'])
async def panel_command():
    try:
        command = request.args['command']
    except KeyError:
        return "Error: No command provided."
    await skaarhoj_panel.send(command)
    return 'OK'


@app.route('/api/v1/motu/mute-toggle', methods=['GET'])
async def mute_toggle():
    if 'bus' not in request.args:
        return "Error: No bus field provided. Please specify bus."
    if 'index' not in request.args:
        return "Error: No index field provided. Please specify index."
    bus = str(request.args['bus'])
    channel = int(request.args['index'])
    path = 'mix/{}/{}/matrix/mute'.format(bus, channel)
    return json.dumps({'status': str(await motu_ds.toggle(path))})


@app.route('/api/v1/motu/mute-status', methods=['GET'])
async def mute_status():
    if 'bus' not in request.args:
        return "Error: No bus field provided. Please specify bus."
    if 'index' not in request.args:
        return "Error: No index field provided. Please specify index."
    bus = str(request.args['bus'])
    channel = int(request.args['index'])
    path = 'mix/{}/{}/matrix/mute'.format(bus, channel)
    return json.dumps({'status': str(motu_ds.get(path))})


@app.route('/api/v1/motu/aux-send-level', methods=['GET'])
async def mix_send_level_get():
    if 'chan' not in request.args:
        return "Error: No channel field provided. Please specify channel #."
    if 'aux' not in request.args:
        return "Error: No aux field provided. Please specify aux #."
    channel = int(request.args['chan'])
    aux = int(request.args['aux'])
    path = 'mix/chan/{}/matrix/aux/{}/send'.format(channel, aux)
    result = motu_ds.get(path)
    try:
        fmt = request.args['format']
    except KeyError:
        pass
    else:
        if fmt in ('db', 'raw'):
            result = motu.level_to_db(result)
        if fmt == 'raw':
            result = motu.db_from_raw(
                result,
                raw_db_range_mapping,
                reverse=True,
            )
    return json.dumps({'status': '{:.10f}'.format(result)})


@app.route('/api/v1/motu/aux-send-level', methods=['POST', 'PATCH'])
async def mix_send_level_set():
    if 'chan' not in request.args:
        return "Error: No channel field provided. Please specify channel #."
    if 'aux' not in request.args:
        return "Error: No aux field provided. Please specify aux #."
    if 'value' not in request.args:
        return "Error: No value field provided. Please specify new value."
    channel = int(request.args['chan'])
    aux = int(request.args['aux'])
    value = float(request.args['value'])
    try:
        fmt = request.args['format']
    except KeyError:
        value = motu.limit_to_range(
            value,
            motu.level_range[0],
            motu.level_range[1],
        )
    else:
        if fmt == 'raw':
            value = motu.db_from_raw(value, raw_db_range_mapping)
        if fmt in ('db', 'raw'):
            value = motu.level_from_db(value)
    path = 'mix/chan/{}/matrix/aux/{}/send'.format(channel, aux)
    await motu_ds.set(path, value)
    result = motu_ds.get(path)
    return json.dumps({'status': '{:.10f}'.format(result)})


if __name__ == "__main__":
    app.run(port='5088')
