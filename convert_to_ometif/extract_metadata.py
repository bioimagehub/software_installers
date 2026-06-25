import argparse
import logging
import os
from bioio import BioImage
import yaml

from nd2reader import ND2Reader
import os
import bioimage_pipeline_utils as rp

logger = logging.getLogger(__name__)

def get_core_metadata(input_file):
    img = rp.load_tczyx_image(input_file)

    # Image dimensions
    t, c, z, y, x = img.dims.T, img.dims.C, img.dims.Z, img.dims.Y, img.dims.X

    # Physical dimensions
    z_um, y_um, x_um = img.physical_pixel_sizes.Z, img.physical_pixel_sizes.Y, img.physical_pixel_sizes.X

    # TODO Find out if time is possible to find

    # Channel info
    channel_info = [str(n) for n in img.channel_names]

    # Extract metadata
    image_metadata = {
        'Image metadata': {
            'Channels': [{'Name': f'Please fill in e.g. {name}'} for name in channel_info],
            'Image dimensions': {'C': c, 'T': t, 'X': x, 'Y': y, 'Z': z},
            'Physical dimensions': {'T_ms': None, 'X_um': x_um, 'Y_um': y_um, 'Z_um': z_um},
        }
    }
    return image_metadata

def get_nd2_roi_metadata(file_path):
    # Read the ND2 file and access metadata
    roi_info = []
    try:
        with ND2Reader(file_path) as images:
            metadata = images.metadata
    
    except Exception as e:
        logger.error(f"Error reading metadata: {e}")
        return None
    
    try:
        # Get the size of a pixel in micrometers
        pixel_microns = metadata.get('pixel_microns', 1)  # Default to 1 if not found
        # Access ROIs from metadata (avoid noisy prints)
        rois = metadata.get('rois', [])
    except Exception as e:
        logger.error(f"Error counting rois: {e}")
        return None

    if len(rois)<1:
        return None

    try:    
        # Loop through each ROI to extract position and size information
        for roi in rois:
            positions = roi.get('positions', [])
            sizes = roi.get('sizes', [])
            shape = roi.get('shape', 'unknown')  # Extract shape if available
            roi_type = roi.get('type', 'unknown')  # Extract type if available

            # Convert position and size from micrometers to pixels
            for pos, size in zip(positions, sizes):
                pos_pixels = [float(p / pixel_microns) for p in pos]
                size_pixels = [float(s / pixel_microns) for s in size]

                roi_pixels = {
                    "Roi": {
                        "Positions": {
                            "x": pos_pixels[0],
                            "y": pos_pixels[1]
                        },
                        "Size": {
                            "x": size_pixels[0],
                            "y": size_pixels[1]
                        },
                        "Shape": shape,
                        "Type": roi_type
                    }
                }

                roi_info.append(roi_pixels)
                    
    except Exception as e:
        logger.error(f"Error processing: {e}")
        return None
    
    return roi_info

def get_all_metadata(input_file, output_file = None):
    
    # Check if output file path is provided, if not set default
    if output_file is None:
        output_file = os.path.splitext(input_file)[0] + "_metadata.yaml"   

    # Get metadata from bioio
    metadata = get_core_metadata(input_file)

    # Get roi info from nd2 files
    if input_file.endswith(".nd2"):
        roi_info = get_nd2_roi_metadata(input_file)
        metadata["Image metadata"]["ROIs"] = roi_info

    # Save metadata to YAML file
    # with open(output_file, 'w') as f:
    #     yaml.dump(metadata, f)
    return metadata

if __name__ == "__main__":


    parser = argparse.ArgumentParser(description="Extract metadata from a BioImage file.")
    parser.add_argument("--input-file", type=str, required=True, help="Path to the input BioImage file")
    parser.add_argument("--output-file", type=str, required=False, help="Path to save the metadata YAML file")
    parser.add_argument("--log-level", type=str, default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level (default: WARNING)")
    parser.add_argument('--input-dims-order', type=str, default=None, help='Optional input dimensions order for array-like inputs (for example ZYX or CZYX).')
    args = parser.parse_args()
    if args.input_dims_order:
        import os
        os.environ['RP_INPUT_DIMS_ORDER'] = args.input_dims_order
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    metadata = get_all_metadata(input_file = args.input_file, output_file= args.output_file)
    print(metadata)
