from quart import Quart, request
import json
import logging
import asyncio
from aioprometheus import MetricsMiddleware
from aioprometheus.asgi.quart import metrics
import motu

logging.basicConfig(level=logging.INFO)
logging.info("Setting LOGLEVEL to INFO")

app = Quart('MOTU API')
app.config["DEBUG"] = True
motu_ds = motu.DataStore()
motu_ms = motu.Meters()

app.asgi_app = MetricsMiddleware(app.asgi_app)
app.add_url_rule('/metrics', 'metrics', metrics, methods=['GET'])


@app.before_serving
async def startup():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(motu_ds.refresh())
        tg.create_task(motu_ms.refresh())
    logging.info("Initial data refresh has completed")
    app.add_background_task(motu_ds.poll)
    app.add_background_task(motu_ms.poll)


@app.route('/', methods=['GET'])
async def home():
    return '<h1>MOTU API</h1>'


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


if __name__ == "__main__":
    app.run(port='5088')
