from flask import Flask, render_template, request, send_file, jsonify # type: ignore
import subprocess
import tempfile
import os
from pathlib import Path
import gzip
import shutil

app = Flask(__name__)

def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download_image():
    image_url = request.form.get('image_url')
    if not image_url:
        return "Image URL is required", 400

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = Path(temp_dir)
        tar_file = temp_dir_path / "image.tar"
        gz_file = temp_dir_path / "image.tar.gz"
        
        try:
            cmd = [
                "skopeo",
                "copy",
                "--insecure-policy",
                f"docker://{image_url}",
                f"docker-archive:{tar_file}"
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            with open(tar_file, 'rb') as f_in:
                with gzip.open(gz_file, 'wb', compresslevel=6) as f_out:
                    shutil.copyfileobj(f_in, f_out)
            
            file_size = os.path.getsize(gz_file)
            formatted_size = format_size(file_size)
            
            response = send_file(
                gz_file,
                as_attachment=True,
                download_name=f"{image_url.replace('/', '_')}.tar.gz",
                mimetype='application/gzip'
            )
            response.headers['X-File-Size'] = formatted_size
            return response
            
        except subprocess.CalledProcessError as e:
            return f"Error downloading image: {e.stderr}", 500
        except Exception as e:
            return f"Unexpected error: {str(e)}", 500

if __name__ == '__main__':
    app.run(debug=True)