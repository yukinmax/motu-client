import urllib.request as req
import json
import argparse

ap = argparse.ArgumentParser()
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


def toggle_channel_matrix(channel, key):
    s = get_channel_matrix(channel)[key]
    j = abs(s - 1)
    r = set_channel_matrix(channel, key, j)
    if r.code == 204:
        return str(int(j))
    else:
        return "FAILURE"


def main():
    print(toggle_channel_matrix(0, 'mute'))

if __name__ == '__main__':
    main()
