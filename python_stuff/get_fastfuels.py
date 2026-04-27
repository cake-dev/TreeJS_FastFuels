import os
import json
import time
import pandas as pd
import geopandas as gpd
from requests import request
import requests
from shapely import Point

os.environ["FASTFUELS_API_KEY"] = "770a09d244dd45d38105dbaa0eb8023d"
FASTFUELS_API_KEY = "770a09d244dd45d38105dbaa0eb8023d"
FASTFUELS_API_URL = "https://api.fastfuels.silvxlabs.com/"

os.environ['AWS_NO_SIGN_REQUEST'] = 'YES'
s3_url = "s3://dataforgood-fb-data/forests/v1/alsgedi_global_v6_float/chm/"

# Load the geospatial ROI polygon. This is the area we want to get CHM and tree inventory data for.
roi_gdf = gpd.read_file("roi.geojson")


# Define a helper function to make requests with the FastFuels API
def request(method, url, **kwargs):
    headers = {"api-key": FASTFUELS_API_KEY}
    headers.update(kwargs.get("headers", {}))
    response = requests.request(method, url, **kwargs)
    response.raise_for_status()  # Raise an exception for bad status codes
    print(url)
    return response

# Get tree inventory data from the FastFuels API
print(type(roi_gdf.to_json()))  # Output: <class 'str'>

# Create a domain resource with our ROI using the FastFuels API
domain_response = request("POST", FASTFUELS_API_URL + "v1/domains", json=json.loads(roi_gdf.to_json()), headers={"api-key": FASTFUELS_API_KEY})
domain_response_json = domain_response.json()
domain_id = domain_response_json["id"]
print(domain_id)  # Output: The generated domain ID

# Create a tree inventory resource for the domain using TreeMap
inventory_response = request("POST", FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree", json={"sources": ["TreeMap"]}, headers={"api-key": FASTFUELS_API_KEY})
inventory_response_json = inventory_response.json()
print(inventory_response_json)  # Output: Details of the created inventory resource

# Refresh the tree inventory resource until status is "completed" (check every 5 seconds)
inventory_completed = False
while not inventory_completed:
    inventory_response = request("GET", FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/", headers={"api-key": FASTFUELS_API_KEY})
    inventory_response_json = inventory_response.json()
    if inventory_response_json["status"] == "completed":
        inventory_completed = True
        export_response = request("POST", FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/exports/csv", headers={"api-key": FASTFUELS_API_KEY})
    else:
        print("Inventory not completed yet. Waiting 5 seconds...")
        time.sleep(5)
print(export_response.json())  # Output: Details of the created export resource

# Refresh the export resource until status is "completed" (check every 5 seconds)
export_completed = False
while not export_completed:
    export_response = request("GET", FASTFUELS_API_URL + f"v1/domains/{domain_id}/inventories/tree/exports/csv/", headers={"api-key": FASTFUELS_API_KEY})
    export_response_json = export_response.json()
    if export_response_json["status"] == "completed":
        export_completed = True
        response = request("GET", export_response_json["signedUrl"])
        with open("tree_inventory_{}.csv".format(domain_id), "wb") as f:
            f.write(response.content)
    else:
        print("Export not completed yet. Waiting 5 seconds...")
        time.sleep(5)
print(export_response.json())  # Output: Details of the export resource

# Convert the tree inventory CSV to a geodataframe and reproject to EPSG:3857
tree_inventory_df = pd.read_csv("tree_inventory_{}.csv".format(domain_id)).dropna()
# Assuming you have a chm_raster (rasterio object)
tree_inventory_gdf = gpd.GeoDataFrame(tree_inventory_df, geometry=[Point(x, y) for x, y in zip(tree_inventory_df["X"], tree_inventory_df["Y"])], crs="EPSG:32612")
tree_inventory_gdf_5070 = tree_inventory_gdf.to_crs("EPSG:5070")
# change X and Y columns to have the values of the geometry
tree_inventory_gdf_5070["X"] = tree_inventory_gdf_5070.geometry.x
tree_inventory_gdf_5070["Y"] = tree_inventory_gdf_5070.geometry.y
tree_inventory_gdf_5070.to_csv("tree_inventory_{}_5070.csv".format(domain_id), index=False)