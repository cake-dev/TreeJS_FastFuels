import tkinter as tk
import json
import os

# Check for required map library
try:
    import tkintermapview
except ImportError:
    output = {
        "success": False, 
        "error": "tkintermapview not installed. Run: pip install tkintermapview"
    }
    try:
        import nitropy
        nitropy.output()
    except ImportError:
        pass
    exit()

class ROIPicker:
    def __init__(self, root, save_path):
        self.root = root
        self.save_path = save_path
        self.markers = []
        self.polygon = None
        self.roi_saved = False

        # Configure Main Window
        self.root.title("Select ROI Bounding Box")
        self.root.geometry("800x600")
        
        # Bring window to front
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after_idle(self.root.attributes, '-topmost', False)

        # Initialize Map Widget
        self.map_widget = tkintermapview.TkinterMapView(self.root, corner_radius=0)
        self.map_widget.pack(fill="both", expand=True)
        self.map_widget.set_position(39.8283, -98.5795)  # Default center: CONUS
        self.map_widget.set_zoom(4)

        # Instructions
        self.info_label = tk.Label(self.root, text="Left Click to set 2 opposite corners of the Bounding Box.", bg="white", font=("Arial", 12))
        self.info_label.pack(side="top", fill="x")

        # Bottom Button Frame
        self.btn_frame = tk.Frame(self.root)
        self.btn_frame.pack(side="bottom", fill="x", pady=5)

        self.clear_btn = tk.Button(self.btn_frame, text="Clear Map", command=self.clear_markers, width=15)
        self.clear_btn.pack(side="left", padx=20)

        # Keep button grey while disabled to visually show it is unclickable
        self.save_btn = tk.Button(self.btn_frame, text="Save ROI", command=self.save_roi, state=tk.DISABLED, width=15, bg="lightgrey", fg="gray")
        self.save_btn.pack(side="right", padx=20)

        # Bind Click Event
        self.map_widget.add_left_click_map_command(self.add_marker)

    def add_marker(self, coords):
        """Adds a marker to the map and checks if bounding box can be drawn."""
        if len(self.markers) < 2:
            marker = self.map_widget.set_marker(coords[0], coords[1], text=f"Corner {len(self.markers)+1}")
            self.markers.append(marker)

        if len(self.markers) == 2:
            self.draw_bbox()
            self.save_btn.config(state=tk.NORMAL, bg="#4CAF50", fg="white")

    def draw_bbox(self):
        """Draws the rectangular bounding box using the 2 marker coordinates."""
        if self.polygon:
            self.polygon.delete()
            self.polygon = None

        lat1, lon1 = self.markers[0].position
        lat2, lon2 = self.markers[1].position

        # Create 4 corners for the rectangular bounding box
        path = [
            (lat1, lon1),
            (lat1, lon2),
            (lat2, lon2),
            (lat2, lon1),
            (lat1, lon1)
        ]
        
        # Using fill_color="" prevents Tkinter stipple artifacts when panning
        self.polygon = self.map_widget.set_polygon(path, outline_color="red", border_width=2, fill_color="")

    def clear_markers(self):
        """Clears all markers and polygons from the map."""
        for marker in self.markers:
            marker.delete()
        if self.polygon:
            self.polygon.delete()
            
        self.markers.clear()
        self.polygon = None
        self.save_btn.config(state=tk.DISABLED, bg="lightgrey", fg="gray")

    def save_roi(self):
        """Constructs GeoJSON and saves to disk."""
        lat1, lon1 = self.markers[0].position
        lat2, lon2 = self.markers[1].position

        # GeoJSON requires coordinates in [Longitude, Latitude] format
        min_lon, max_lon = min(lon1, lon2), max(lon1, lon2)
        min_lat, max_lat = min(lat1, lat2), max(lat1, lat2)

        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [min_lon, min_lat], 
                        [max_lon, min_lat], 
                        [max_lon, max_lat], 
                        [min_lon, max_lat], 
                        [min_lon, min_lat]
                    ]]
                }
            }]
        }

        # Ensure directory exists before saving
        os.makedirs(os.path.dirname(self.save_path), exist_ok=True)

        with open(self.save_path, 'w') as f:
            json.dump(geojson, f, indent=4)

        self.roi_saved = True
        self.root.quit()
        self.root.destroy()


# ==========================================
# Script Execution & Nitro Output
# ==========================================

# Use the user's Documents folder as a default safe path
output_filepath = os.path.join(os.path.expanduser("~"), "Documents", "selected_roi.geojson")

# Launch GUI
root = tk.Tk()
app = ROIPicker(root, output_filepath)
root.mainloop()

# Package data for Nitro Python Runtime
output = {
    "success": app.roi_saved,
    "saved_path": output_filepath if app.roi_saved else ""
}

# Broadcast the output variable back to Unreal Engine Blueprint
try:
    import nitropy
    nitropy.output()
except ImportError:
    # Handle direct execution outside of Unreal Engine
    print(f"Output generated:\nSuccess: {output['success']}\nPath: {output['saved_path']}")