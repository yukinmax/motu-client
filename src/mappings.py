"""Static HWC and feedback tables."""

control_map = {
    '13': {
        'path': 'mix/chan/0/matrix/aux/0/send',
    },
    '14': {
        'path': 'mix/chan/2/matrix/aux/0/send',
    },
    '15': {
        'path': 'mix/chan/4/matrix/aux/0/send',
    },
    '16': {
        'path': 'mix/chan/6/matrix/aux/0/send',
    },
    '17': {
        'path': 'mix/chan/8/matrix/aux/0/send',
    },
    '18': {
        'path': 'mix/group/0/matrix/main/0/send',
    },
    '19': {
        'path': 'mix/aux/0/matrix/fader',
    },
    '20': {
        'path': 'mix/main/0/matrix/fader',
    },
    '61.4': {
        'path': 'mix/chan/0/matrix/mute',
    },
    '64.4': {
        'path': 'mix/chan/2/matrix/mute',
    },
    '67.4': {
        'path': 'mix/chan/4/matrix/mute',
    },
    '70.4': {
        'path': 'mix/chan/6/matrix/mute',
    },
    '73.4': {
        'path': 'mix/chan/8/matrix/mute',
    },
    '76.4': {
        'path': 'mix/group/0/matrix/mute',
    },
    '79.4': {
        'path': 'mix/aux/0/matrix/mute',
    },
    '82.4': {
        'path': 'mix/main/0/matrix/mute',
    },
    '62.1': {
        'path': 'mix/chan/0/matrix/solo',
    },
    '65.1': {
        'path': 'mix/chan/2/matrix/solo',
    },
    '68.1': {
        'path': 'mix/chan/4/matrix/solo',
    },
    '71.1': {
        'path': 'mix/chan/6/matrix/solo',
    },
    '74.1': {
        'path': 'mix/chan/8/matrix/solo',
    },
    '77.1': {
        'path': 'mix/group/0/matrix/solo',
    },
    '80.1': {
        'path': 'mix/monitor/0/override',
        'override_index': 0,
    },
    '63.4': {
        'path': 'mix/chan/0/matrix/aux/0/send',
        'default_level': -5.0,
    },
    '66.4': {
        'path': 'mix/chan/2/matrix/aux/0/send',
        'default_level': -10.0,
    },
    '69.4': {
        'path': 'mix/chan/4/matrix/aux/0/send',
        'default_level': -20.0,
    },
    '72.4': {
        'path': 'mix/chan/6/matrix/aux/0/send',
    },
    '75.4': {
        'path': 'mix/chan/8/matrix/aux/0/send',
    },
    '78.4': {
        'path': 'mix/group/0/matrix/main/0/send',
    },
    '81.4': {
        'path': 'mix/aux/0/matrix/fader',
    },
    '84.4': {
        'path': 'mix/main/0/matrix/fader',
    },
}


feedback_map = {
    'mix/chan/0/matrix/mute': {
        'button': {
            'hwcid': 61,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/2/matrix/mute': {
        'button': {
            'hwcid': 64,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/4/matrix/mute': {
        'button': {
            'hwcid': 67,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/6/matrix/mute': {
        'button': {
            'hwcid': 70,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/8/matrix/mute': {
        'button': {
            'hwcid': 73,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/group/0/matrix/mute': {
        'button': {
            'hwcid': 76,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/aux/0/matrix/mute': {
        'button': {
            'hwcid': 79,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/main/0/matrix/mute': {
        'button': {
            'hwcid': 82,
            'mode': {
                'state': 4
            },
            'color': {
                'index': (4, 15)
            }
        }
    },
    'mix/chan/0/matrix/solo': {
        'button': {
            'hwcid': 62,
            'mode': {
                'state': (4, 0)
            },
            'color': {
                'index': (8, 0)
            }
        }
    },
    'mix/chan/2/matrix/solo': {
        'button': {
            'hwcid': 65,
            'mode': {
                'state': (4, 0)
            },
            'color': {
                'index': (8, 0)
            }
        }
    },
    'mix/chan/4/matrix/solo': {
        'button': {
            'hwcid': 68,
            'mode': {
                'state': (4, 0)
            },
            'color': {
                'index': (8, 0)
            }
        }
    },
    'mix/chan/6/matrix/solo': {
        'button': {
            'hwcid': 71,
            'mode': {
                'state': (4, 0)
            },
            'color': {
                'index': (8, 0)
            }
        }
    },
    'mix/chan/8/matrix/solo': {
        'button': {
            'hwcid': 74,
            'mode': {
                'state': (4, 0)
            },
            'color': {
                'index': (8, 0)
            }
        }
    },
    'mix/group/0/matrix/solo': {
        'button': {
            'hwcid': 77,
            'mode': {
                'state': (4, 0)
            },
            'color': {
                'index': (8, 0)
            }
        }
    },
    'mix/chan/0/matrix/aux/0/send': {
        'fader': {
            'hwcid': 13,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 29,
            'text': {
                'formatting': 7,
                'title': 'Mic',
                'solid_header': True
            }
        }
    },
    'mix/chan/2/matrix/aux/0/send': {
        'fader': {
            'hwcid': 14,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 30,
            'text': {
                'formatting': 7,
                'title': 'PC',
                'solid_header': True
            }
        }
    },
    'mix/chan/4/matrix/aux/0/send': {
        'fader': {
            'hwcid': 15,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 31,
            'text': {
                'formatting': 7,
                'title': 'Music',
                'solid_header': True
            }
        }
    },
    'mix/chan/6/matrix/aux/0/send': {
        'fader': {
            'hwcid': 16,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 32,
            'text': {
                'formatting': 7,
                'title': 'Comm',
                'solid_header': True
            }
        }
    },
    'mix/chan/8/matrix/aux/0/send': {
        'fader': {
            'hwcid': 17,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 13
            }
        },
        'display': {
            'hwcid': 33,
            'text': {
                'formatting': 7,
                'title': 'Anlg',
                'solid_header': True
            }
        }
    },
    'mix/group/0/matrix/main/0/send': {
        'fader': {
            'hwcid': 18,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 7
            }
        },
        'display': {
            'hwcid': 34,
            'text': {
                'formatting': 7,
                'title': 'VOD',
                'solid_header': True
            }
        }
    },
    'mix/aux/0/matrix/fader': {
        'fader': {
            'hwcid': 19,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 11
            }
        },
        'display': {
            'hwcid': 35,
            'text': {
                'formatting': 7,
                'title': 'MyMix',
                'solid_header': True
            }
        }
    },
    'mix/main/0/matrix/fader': {
        'fader': {
            'hwcid': 20,
            'mode': {
                'state': 4
            },
            'color': {
                'index': 11
            }
        },
        'display': {
            'hwcid': 36,
            'text': {
                'formatting': 7,
                'title': 'Main',
                'solid_header': True
            }
        }
    },
    'mix/monitor/0/override': {
        'button1': {
            'hwcid': 80,
            'value': 1024,
            'mode': {
                'state': (4, 0)
            },
            'color': {
                'index': (8, 0)
            }
        }
    },
    'mix/level': {
        'display1': {
            'hwcid': 37,
            'channels': [0],
            'pre_fader': False,
            'fader_path': 'mix/chan/0/matrix/aux/0/send',
            'mute_path': 'mix/chan/0/matrix/mute',
            'audio_meter': {
                'mono': True,
                'meter_type': 1,
            }
        },
        'display2': {
            'hwcid': 38,
            'channels': [2, 3],
            'pre_fader': False,
            'fader_path': 'mix/chan/2/matrix/aux/0/send',
            'mute_path': 'mix/chan/2/matrix/mute',
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        },
        'display3': {
            'hwcid': 39,
            'channels': [4, 5],
            'pre_fader': False,
            'fader_path': 'mix/chan/4/matrix/aux/0/send',
            'mute_path': 'mix/chan/4/matrix/mute',
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        },
        'display4': {
            'hwcid': 40,
            'channels': [6, 7],
            'pre_fader': False,
            'fader_path': 'mix/chan/6/matrix/aux/0/send',
            'mute_path': 'mix/chan/6/matrix/mute',
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        },
        'display5': {
            'hwcid': 41,
            'channels': [8, 9],
            'pre_fader': False,
            'fader_path': 'mix/chan/8/matrix/aux/0/send',
            'mute_path': 'mix/chan/8/matrix/mute',
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        },
        'display6': {
            'hwcid': 42,
            'channels': [34, 35],
            'pre_fader': False,
            'fader_path': 'mix/group/0/matrix/main/0/send',
            'mute_path': 'mix/group/0/matrix/mute',
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        },
        'display7': {
            'hwcid': 43,
            'channels': [40, 41],
            'pre_fader': False,
            'fader_path': 'mix/aux/0/matrix/fader',
            'mute_path': 'mix/aux/0/matrix/mute',
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        },
        'display8': {
            'hwcid': 44,
            'channels': [38, 39],
            'pre_fader': False,
            'fader_path': 'mix/main/0/matrix/fader',
            'mute_path': 'mix/main/0/matrix/mute',
            'audio_meter': {
                'mono': False,
                'meter_type': 1,
            }
        }
    }
}
