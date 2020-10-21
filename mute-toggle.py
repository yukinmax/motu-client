import urllib.request as req
import json
import argparse

ap = argparse.ArgumentParser()
ap.add_argument("channel",
                type=int,
                default=0,
                help="Mute selected channel")
ap.add_argument("--hostname",
                type=str,
                default="ultralite-avb.local",
                help="Hostname of MOTU device")
args = ap.parse_args()


def get_channel_matrix(c):
    r = req.Request(
        'http://{}/datastore/mix/chan/{}/matrix'.format(
            args.hostname,
            c
        )
    )
    with req.urlopen(r) as f:
        return json.loads(f.read())


def set_channel_matrix(c, key, value):
    r = req.Request(
        'http://{}/datastore/mix/chan/{}/matrix'.format(
            args.hostname,
            c
        ),
        data='json={}'.format(json.dumps({key: value})).encode(),
        method='PATCH'
    )
    with req.urlopen(r) as f:
        pass
    return f


def main():
    s = 0
    if get_channel_matrix(args.channel)['mute'] == 0:
        s = 1
    set_channel_matrix(args.channel, 'mute', s)


if __name__ == '__main__':
    main()
