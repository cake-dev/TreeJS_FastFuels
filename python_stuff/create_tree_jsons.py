
import json
import os
import pandas as pd
from copy import deepcopy
from collections import Counter

DEFAULT_TREE = {
    "seed": 11744,
    "type": "evergreen",
    "bark": {
        "type": "pine",
        "tint": 16761758,
        "flatShading": False,
        "textured": True,
        "textureScale": {
            "x": 1,
            "y": 1
        }
    },
    "branch": {
        "levels": 1,
        "angle": {
            "1": 110,
            "2": 60,
            "3": 60
        },
        "children": {
            "0": 91,
            "1": 7,
            "2": 5
        },
        "force": {
            "direction": {
                "x": 0,
                "y": 1,
                "z": 0
            },
            "strength": 0.01
        },
        "gnarliness": {
            "0": 0,
            "1": 0,
            "2": 0,
            "3": 0
        },
        "length": {
            "0": 35,
            "1": 10,
            "2": 10,
            "3": 1
        },
        "radius": {
            "0": 0.7,
            "1": 0.45,
            "2": 0.7,
            "3": 0.7
        },
        "sections": {
            "0": 12,
            "1": 10,
            "2": 8,
            "3": 6
        },
        "segments": {
            "0": 8,
            "1": 6,
            "2": 4,
            "3": 3
        },
        "start": {
            "1": 0.36,
            "2": 0.3,
            "3": 0.3
        },
        "taper": {
            "0": 0.65,
            "1": 0.74,
            "2": 0.7,
            "3": 0.7
        },
        "twist": {
            "0": 0,
            "1": 0,
            "2": 0,
            "3": 0
        }
    },
    "leaves": {
        "type": "pine",
        "billboard": "double",
        "angle": 10,
        "count": 15,
        "start": 0,
        "size": 1.31,
        "sizeVariance": 0.16,
        "tint": 16777215,
        "alphaTest": 0.3
    }
}

TREE_PONDEROSA_PINE_122 = {
  "seed": 28020,
  "type": "evergreen",
  "bark": {
    "type": "pine",
    "tint": 16761758,
    "flatShading": False,
    "textured": True,
    "textureScale": {
      "x": 1,
      "y": 1
    }
  },
  "branch": {
    "levels": 1,
    "angle": {
      "1": 121,
      "2": 60,
      "3": 60
    },
    "children": {
      "0": 75,
      "1": 7,
      "2": 5
    },
    "force": {
      "direction": {
        "x": 0,
        "y": 1,
        "z": 0
      },
      "strength": 0.01
    },
    "gnarliness": {
      "0": 0,
      "1": 0.01,
      "2": 0.15,
      "3": 0
    },
    "length": {
      "0": 40,
      "1": 10,
      "2": 10,
      "3": 1
    },
    "radius": {
      "0": 0.5,
      "1": 0.41,
      "2": 0.7,
      "3": 0.7
    },
    "sections": {
      "0": 12,
      "1": 10,
      "2": 8,
      "3": 6
    },
    "segments": {
      "0": 6,
      "1": 6,
      "2": 4,
      "3": 3
    },
    "start": {
      "1": 0.33,
      "2": 0.3,
      "3": 0.3
    },
    "taper": {
      "0": 0.7,
      "1": 0.7,
      "2": 0.7,
      "3": 0.7
    },
    "twist": {
      "0": 0,
      "1": 0,
      "2": 0,
      "3": 0
    }
  },
  "leaves": {
    "type": "pine",
    "billboard": "double",
    "angle": 16,
    "count": 20,
    "start": 0.33,
    "size": 1.25,
    "sizeVariance": 0.7,
    "tint": 16777215,
    "alphaTest": 0.3
  }
}

TREE_DOUGLAS_FIR_202 = {
  "seed": 25519,
  "type": "evergreen",
  "bark": {
    "type": "pine",
    "tint": 16761758,
    "flatShading": False,
    "textured": True,
    "textureScale": {
      "x": 1,
      "y": 1
    }
  },
  "branch": {
    "levels": 1,
    "angle": {
      "1": 96,
      "2": 60,
      "3": 60
    },
    "children": {
      "0": 75,
      "1": 7,
      "2": 5
    },
    "force": {
      "direction": {
        "x": 0,
        "y": 1,
        "z": 0
      },
      "strength": 0.0104
    },
    "gnarliness": {
      "0": 0,
      "1": 0.01,
      "2": 0.15,
      "3": 0
    },
    "length": {
      "0": 40,
      "1": 10,
      "2": 10,
      "3": 1
    },
    "radius": {
      "0": 1,
      "1": 0.41,
      "2": 0.7,
      "3": 0.7
    },
    "sections": {
      "0": 12,
      "1": 10,
      "2": 8,
      "3": 6
    },
    "segments": {
      "0": 6,
      "1": 6,
      "2": 4,
      "3": 3
    },
    "start": {
      "1": 0.33,
      "2": 0.3,
      "3": 0.3
    },
    "taper": {
      "0": 0.7,
      "1": 0.7,
      "2": 0.7,
      "3": 0.7
    },
    "twist": {
      "0": 0,
      "1": 0,
      "2": 0,
      "3": 0
    }
  },
  "leaves": {
    "type": "pine",
    "billboard": "double",
    "angle": 16,
    "count": 20,
    "start": 0,
    "size": 1.25,
    "sizeVariance": 0.7,
    "tint": 16777215,
    "alphaTest": 0.3
  }
}

TREE_JUNIPER_66 = {
  "seed": 25519,
  "type": "evergreen",
  "bark": {
    "type": "birch",
    "tint": 16761758,
    "flatShading": False,
    "textured": True,
    "textureScale": {
      "x": 1,
      "y": 1
    }
  },
  "branch": {
    "levels": 1,
    "angle": {
      "1": 50,
      "2": 60,
      "3": 60
    },
    "children": {
      "0": 75,
      "1": 7,
      "2": 5
    },
    "force": {
      "direction": {
        "x": 0,
        "y": 1,
        "z": 0
      },
      "strength": 0.0104
    },
    "gnarliness": {
      "0": 0,
      "1": 0.01,
      "2": 0.15,
      "3": 0
    },
    "length": {
      "0": 40,
      "1": 10,
      "2": 10,
      "3": 1
    },
    "radius": {
      "0": 1,
      "1": 0.41,
      "2": 0.7,
      "3": 0.7
    },
    "sections": {
      "0": 12,
      "1": 10,
      "2": 8,
      "3": 6
    },
    "segments": {
      "0": 6,
      "1": 6,
      "2": 4,
      "3": 3
    },
    "start": {
      "1": 0.15,
      "2": 0.3,
      "3": 0.3
    },
    "taper": {
      "0": 0.7,
      "1": 0.7,
      "2": 0.7,
      "3": 0.7
    },
    "twist": {
      "0": 0,
      "1": 0,
      "2": 0,
      "3": 0
    }
  },
  "leaves": {
    "type": "pine",
    "billboard": "double",
    "angle": 16,
    "count": 20,
    "start": 0,
    "size": 1.75,
    "sizeVariance": 0.7,
    "tint": 16777215,
    "alphaTest": 0.3
  }
}

TREE_COTTONWOOD_742 = {
  "seed": 22033,
  "type": "deciduous",
  "bark": {
    "type": "oak",
    "tint": 16774097,
    "flatShading": False,
    "textured": True,
    "textureScale": {
      "x": 1,
      "y": 10
    }
  },
  "branch": {
    "levels": 3,
    "angle": {
      "1": 63,
      "2": 54,
      "3": 60
    },
    "children": {
      "0": 7,
      "1": 6,
      "2": 3
    },
    "force": {
      "direction": {
        "x": 0,
        "y": 1,
        "z": 0
      },
      "strength": 0.0188
    },
    "gnarliness": {
      "0": -0.04,
      "1": 0.22,
      "2": 0.21,
      "3": -0.12
    },
    "length": {
      "0": 27.14,
      "1": 17.34,
      "2": 11.46,
      "3": 5.59
    },
    "radius": {
      "0": 2,
      "1": 0.63,
      "2": 0.36,
      "3": 0.56
    },
    "sections": {
      "0": 16,
      "1": 9,
      "2": 8,
      "3": 1
    },
    "segments": {
      "0": 7,
      "1": 5,
      "2": 3,
      "3": 3
    },
    "start": {
      "1": 0,
      "2": 0.46,
      "3": 0.08
    },
    "taper": {
      "0": 0.49,
      "1": 0.43,
      "2": 0.69,
      "3": 0.75
    },
    "twist": {
      "0": 0.06,
      "1": -0.12,
      "2": 0,
      "3": 0
    }
  },
  "leaves": {
    "type": "oak",
    "billboard": "double",
    "angle": 53,
    "count": 15,
    "start": 0.164,
    "size": 1.62,
    "sizeVariance": 0.7,
    "tint": "0xffffff",
    "alphaTest": 0.5
  }
}

TREE_ASPEN_746 = {
  "seed": 18020,
  "type": "deciduous",
  "bark": {
    "type": "birch",
    "tint": 16777215,
    "flatShading": False,
    "textured": True,
    "textureScale": {
      "x": 1,
      "y": 1
    }
  },
  "branch": {
    "levels": 2,
    "angle": {
      "1": 75,
      "2": 32,
      "3": 7
    },
    "children": {
      "0": 10,
      "1": 3,
      "2": 3
    },
    "force": {
      "direction": {
        "x": 0,
        "y": 1,
        "z": 0
      },
      "strength": 0.0148
    },
    "gnarliness": {
      "0": 0.05,
      "1": 0.12,
      "2": 0.12,
      "3": 0.02
    },
    "length": {
      "0": 50,
      "1": 6.07,
      "2": 11.19,
      "3": 1
    },
    "radius": {
      "0": 0.72,
      "1": 0.41,
      "2": 0.7,
      "3": 0.7
    },
    "sections": {
      "0": 12,
      "1": 10,
      "2": 8,
      "3": 6
    },
    "segments": {
      "0": 8,
      "1": 6,
      "2": 4,
      "3": 3
    },
    "start": {
      "1": 0.59,
      "2": 0.35,
      "3": 0
    },
    "taper": {
      "0": 0.37,
      "1": 0.13,
      "2": 0.7,
      "3": 0.7
    },
    "twist": {
      "0": 0,
      "1": 0,
      "2": 0,
      "3": 0
    }
  },
  "leaves": {
    "type": "aspen",
    "billboard": "double",
    "angle": 30,
    "count": 11,
    "start": 0.124,
    "size": 2.5,
    "sizeVariance": 0.7,
    "tint": 16775778,
    "alphaTest": 0.5
  }
}

TREE_ASH_544 = {
  "seed": 31701,
  "type": "deciduous",
  "bark": {
    "type": "oak",
    "tint": 13552830,
    "flatShading": False,
    "textured": True,
    "textureScale": {
      "x": 1,
      "y": 3
    }
  },
  "branch": {
    "levels": 3,
    "angle": {
      "1": 48,
      "2": 75,
      "3": 60
    },
    "children": {
      "0": 5,
      "1": 4,
      "2": 3
    },
    "force": {
      "direction": {
        "x": 0,
        "y": 1,
        "z": 0
      },
      "strength": 0.0158
    },
    "gnarliness": {
      "0": 0.01,
      "1": 0.25,
      "2": 0.2,
      "3": 0.09
    },
    "length": {
      "0": 30,
      "1": 20,
      "2": 9.51,
      "3": 4.6
    },
    "radius": {
      "0": 2,
      "1": 0.63,
      "2": 0.76,
      "3": 0.7
    },
    "sections": {
      "0": 12,
      "1": 10,
      "2": 10,
      "3": 10
    },
    "segments": {
      "0": 8,
      "1": 6,
      "2": 4,
      "3": 3
    },
    "start": {
      "1": 0.19,
      "2": 0.33,
      "3": 0
    },
    "taper": {
      "0": 0.7,
      "1": 0.7,
      "2": 0.7,
      "3": 0.7
    },
    "twist": {
      "0": 0.13,
      "1": -0.07,
      "2": 0,
      "3": 0
    }
  },
  "leaves": {
    "type": "ash",
    "billboard": "double",
    "angle": 55,
    "count": 10,
    "start": 0,
    "size": 2.665,
    "sizeVariance": 0.717,
    "tint": "0xfcff8c",
    "alphaTest": 0.5
  }
}


def generate_tree_json(spcd, cr, avg_ht, avg_dia):
    # tree = deepcopy(DEFAULT_TREE)
    if spcd == 122 or spcd == 113:
        tree = deepcopy(TREE_PONDEROSA_PINE_122)
    elif spcd == 202 or spcd == 17:
        tree = deepcopy(TREE_DOUGLAS_FIR_202)
    elif spcd == 66 or spcd == 64:
        tree = deepcopy(TREE_JUNIPER_66)
    elif spcd == 544:
        tree = deepcopy(TREE_ASH_544)
    elif spcd == 742:
        tree = deepcopy(TREE_COTTONWOOD_742)
    elif spcd == 746:
        tree = deepcopy(TREE_ASPEN_746)
    else:
        tree = deepcopy(DEFAULT_TREE)
    tree["branch"]["start"]["1"] = 1 - cr 
    # reduce children as 1-cr increases
    tree["branch"]["children"]["0"] = 75 - int(70 * (1-cr))
    # spcd specific settings
    # TODO: expiriment with treejs to find the best settings for each tree
    # if spcd == 122: # ponderosa pine
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 202: # douglas-fir
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 66: # rocky mountain juniper
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2) 
    # elif spcd == 544: # green ash
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 742: # eastern cottonwood
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 17: # grand fir
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 746: # quaking aspen
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 64: # Western juniper
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 113: # limber pine
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 823: # bur oak
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 745: # plains cottonwood
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 972: # american elm
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    # elif spcd == 73: # western larch
    #     tree["branch"]["length"]["0"] = round(avg_ht, 2) + 3
    #     tree["branch"]["radius"]["0"] = round((avg_dia - 2.0) / (100 - 2.0) * 1.9 + 0.1, 2)
    return tree

def load_data_and_get_params(csv_path):
    data = pd.read_csv(csv_path)
    data['CR'] = data['CR'].fillna(0.5)
    data['HT'] = data['HT'].fillna(10)
    return data

def get_top_cr_values(data, spcd, n=5):
    cr_values = data[data['SPCD'] == spcd]['CR'].round(1)
    return [cr for cr, _ in Counter(cr_values).most_common(n)]

def create_json_files(data):
    if not os.path.exists("tree_jsons"):
        os.makedirs("tree_jsons")

    unique_spcds = data['SPCD'].unique()

    for spcd in unique_spcds:
        top_cr_values = get_top_cr_values(data, spcd)
        
        for cr in top_cr_values:
            spcd_cr_data = data[(data['SPCD'] == spcd) & (data['CR'].round(2) == cr)]
            avg_ht = spcd_cr_data['HT'].mean()
            avg_dia = spcd_cr_data['DIA'].mean()
            
            tree_data = generate_tree_json(spcd, cr, avg_ht, avg_dia)
            filename = f"tree_jsons/tree_spcd_{spcd}_cr_{cr:.2f}.json"
            
            with open(filename, "w") as f:
                json.dump(tree_data, f, indent=2)
            print(f"Created {filename} with SPCD: {spcd}, CR: {cr:.2f}")

if __name__ == "__main__":
    # define your csv path to be the file saved from get_fastfuels.py
    # csv_path = "C:\\Users\\Jake\\Documents\\Code\\FastFuels\\data\\tree_inventory_6a3f1699c9e44808abbf32eff9b4da20.csv"
    data = load_data_and_get_params(csv_path)
    create_json_files(data)