# TreeJS_FastFuels

# TreeJS_FastFuels is a JavaScript library for creating 3D gltf models of trees from Fast Fuels data.

## Note: this has only been run on Windows, and may not work as configured on other systems.

## Installation and Requirements
- Clone the repository:
```bash
git clone https://github.com/cake-dev/TreeJS_FastFuels.git
cd TreeJS_FastFuels
cd treejs/tree-js
```
- Install the required Python packages (may need others depending on your system):
```bash
pip install pandas shapely geopandas
```
- Install the npm environment (run from treejs/tree-js):
```bash
npm install
npm run build:npm
```
## Getting Fast Fuels Data
- run the get_fastfuels.py script in python_stuff/ with your desired roi located in TreeJS_FastFuels/data/roi.geojson.
```bash
18: roi_gdf = gpd.read_file("data/roi.geojson")
```

## Creating Tree Configs
- run the create_tree_jsons.py script in python_stuff/ with the correct path to the Fast Fuels csv.
```bash
762: csv_path = "YOUR_PATH_TO_FAST_FUELS_CSV"
763: data = load_data_and_get_params(csv_path)
```
- Note: the script will create a folder called tree_jsons in the python_stuff directory.  If there are trees already, delete them before running.

## Running the Demo and Generating Trees
- run the demo with the following command:
```bash
npm run demo
```
- The demo will run on a local server.  Click the link in the terminal to open the demo in your browser. 
- Here you can play with the tree creation parameters and generate trees.
- To use mass tree creation click the 'Mass Create Trees' button.  This will open a file dialog.  Navigate to the tree_jsons folder and select it (the input is the whole folder, not the files within)
    - The tree creation process exports in batches.  You can monitor progress in the developer console (F12) in your browser.  You will be prompted to save every 50 trees due to the batching limitations of the gltf exporter.

### TreeJS Note
- For more info on how to use TreeJS, see the readme in the treejs/tree-js directory. 