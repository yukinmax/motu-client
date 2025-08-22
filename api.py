from quart import Quart, request
import json
import logging
from aioprometheus import MetricsMiddleware
from aioprometheus.asgi.quart import metrics
import motu
import raw_panel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
level = logger.getEffectiveLevel()
level_str = logging.getLevelName(level)
logging.info("Setting LOGLEVEL to {}".format(level_str))

app = Quart('MOTU API')
app.config["DEBUG"] = True
motu_ds = motu.DataStore('ultralite-avb.ynet')
motu_ms = motu.Meters('ultralite-avb.ynet')
skaarhoj_panel = raw_panel.RawPanel('waveboard.ynet',
                                    delay=0.001,
                                    sleep_timeout=600000)
skaarhoj_panel.set_ds(motu_ds)
skaarhoj_panel.set_ms(motu_ms)
motu_ds.set_change_handler(skaarhoj_panel.process_data_feedback)
motu_ms.set_change_handler(skaarhoj_panel.process_meters_feedback)

app.asgi_app = MetricsMiddleware(app.asgi_app)
app.add_url_rule('/metrics', 'metrics', metrics, methods=['GET'])

raw_db_range_mapping = raw_panel.raw_db_range_mapping


@app.before_serving
async def startup():
    app.add_background_task(skaarhoj_panel.handle_requests)
    app.add_background_task(skaarhoj_panel.process_buffers)
    # await skaarhoj_panel.connect()
    await motu_ds.refresh()
    await motu_ms.refresh()
    logging.info("Initial data refresh has completed")
    app.add_background_task(motu_ds.poll)
    app.add_background_task(motu_ms.poll)
    await skaarhoj_panel.connect()
    # app.add_background_task(skaarhoj_panel.handle_requests)
    # app.add_background_task(skaarhoj_panel.process_buffers)


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
    return json.dumps({'status': str(await motu_ds.get(path))})


@app.route('/api/v1/motu/aux-send-level', methods=['GET'])
async def mix_send_level_get():
    if 'chan' not in request.args:
        return "Error: No channel field provided. Please specify channel #."
    if 'aux' not in request.args:
        return "Error: No aux field provided. Please specify aux #."
    channel = int(request.args['chan'])
    aux = int(request.args['aux'])
    path = 'mix/chan/{}/matrix/aux/{}/send'.format(channel, aux)
    result = await motu_ds.get(path)
    try:
        fmt = request.args['format']
    except KeyError:
        pass
    else:
        if fmt in ('db', 'raw'):
            result = await motu.level_to_db(result)
        if fmt == 'raw':
            result = await motu.db_from_raw(result,
                                            raw_db_range_mapping,
                                            reverse=True)
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
        value = await motu.limit_to_range(value,
                                          motu.level_range[0],
                                          motu.level_range[1])
    else:
        if fmt == 'raw':
            value = await motu.db_from_raw(value, raw_db_range_mapping)
        if fmt in ('db', 'raw'):
            value = await motu.level_from_db(value)
    path = 'mix/chan/{}/matrix/aux/{}/send'.format(channel, aux)
    await motu_ds.set(path, value)
    result = await motu_ds.get(path)
    return json.dumps({'status': '{:.10f}'.format(result)})


if __name__ == "__main__":
    app.run(port='5088')
