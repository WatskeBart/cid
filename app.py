
from flask import Flask, render_template, request, send_file, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from datetime import datetime
import subprocess
import tempfile
import os
from pathlib import Path
import gzip
import shutil
import json
import traceback
import logging
from logging.handlers import RotatingFileHandler

app = Flask(__name__, static_folder='static')

APP_VERSION = os.getenv('APP_VERSION', '1.1.1')

# Rate error handler
def rate_limit_exceeded_handler(request_limit):
    return jsonify({
        "status": "error",
        "message": "Rate limit exceeded. Please try again later.",
        "retry_after": request_limit.reset_at - datetime.now().timestamp()
    }), 429

# Rate limiter
limiter = Limiter(
    get_remote_address, # Limit per IP-address
    app=app,
    default_limits=["400 per day", "100 per hour"],
    storage_uri="memory://",
    strategy="fixed-window",
    on_breach=rate_limit_exceeded_handler,
)

# Configure logging
if os.environ.get('FLASK_ENV') == 'production':
    if not os.path.exists('logs'):
        os.mkdir('logs')
    file_handler = RotatingFileHandler('logs/app.log', maxBytes=10240, backupCount=10)
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
    app.logger.info('Container Image Downloader startup')

def format_size(size_bytes):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} GB"

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

@app.route('/')
def index():
    return render_template('index.html', version=APP_VERSION)

@app.route('/download', methods=['POST'])
@limiter.limit("20/minute")
def download_image():
    image_url = request.form.get('image_url')
    if not image_url:
        return jsonify({
            "status": "error",
            "message": "Image URL is required"
        }), 400
    
    action = request.form.get('action', 'download')
    
    if action == 'push':
        return push_image(image_url)
    
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
            return jsonify({
                "status": "error",
                "message": f"Error downloading image: {e.stderr}"
            }), 500
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"Unexpected error: {str(e)}"
            }), 500

def push_image(source_image_url):
    """Push an image from source registry to destination registry"""
    try:
        dest_registry = request.form.get('dest_registry')
        dest_username = request.form.get('dest_username')
        dest_password = request.form.get('dest_password')
        insecure_policy = request.form.get('insecure_policy') == 'on'
        skip_tls_verify = request.form.get('skip_tls_verify') == 'on'
        
        if not dest_registry:
            return jsonify({
                "status": "error",
                "message": "Destination registry is required"
            }), 400
        
        image_parts = source_image_url.split('/')
        if len(image_parts) == 1:
            image_name = image_parts[0]
            dest_image_url = f"{dest_registry}/{image_name}"
        else:
            image_name = '/'.join(image_parts[1:])
            dest_image_url = f"{dest_registry}/{image_name}"
        
        cmd = ["skopeo", "copy"]
        
        if insecure_policy:
            cmd.append("--insecure-policy")
            
        if skip_tls_verify:
            cmd.append("--dest-tls-verify=false")
        
        if dest_username and dest_password:
            cmd.extend([
                "--dest-creds", 
                f"{dest_username}:{dest_password}"
            ])
        
        cmd.extend([
            f"docker://{source_image_url}",
            f"docker://{dest_image_url}"
        ])
        
        # Redact password from logging
        log_cmd = cmd.copy()
        if dest_username and dest_password:
            creds_index = log_cmd.index("--dest-creds") + 1
            log_cmd[creds_index] = f"{dest_username}:***"
        app.logger.info(f"Executing command: {' '.join(log_cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        return jsonify({
            "status": "success",
            "message": f"Image {source_image_url} successfully pushed to {dest_image_url}",
            "details": result.stdout
        })
            
    except subprocess.CalledProcessError as e:
        app.logger.error(f"Command error: {e.stderr}")
        return jsonify({
            "status": "error",
            "message": f"Error pushing image: {e.stderr}"
        }), 500
    except Exception as e:
        error_traceback = traceback.format_exc()
        app.logger.error(f"Unexpected error: {str(e)}\n{error_traceback}")
        return jsonify({
            "status": "error",
            "message": f"Unexpected error: {str(e)}"
        }), 500

@app.route('/batch-push', methods=['POST'])
@limiter.limit("20/minute")
def batch_push():
    """Process multiple images in a batch operation"""
    source_images = request.form.get('source_images', '')
    dest_registry = request.form.get('dest_registry')
    dest_username = request.form.get('dest_username')
    dest_password = request.form.get('dest_password')
    insecure_policy = request.form.get('insecure_policy') == 'on'
    skip_tls_verify = request.form.get('skip_tls_verify') == 'on'
    
    if not source_images or not dest_registry:
        return jsonify({
            "status": "error",
            "message": "Source images and destination registry are required"
        }), 400
    
    image_list = [img.strip() for img in source_images.split('\n') if img.strip()]
    results = []
    
    for image_url in image_list:
        try:
            image_parts = image_url.split('/')
            if len(image_parts) == 1:
                image_name = image_parts[0]
            else:
                image_name = '/'.join(image_parts[1:])
                
            dest_image_url = f"{dest_registry}/{image_name}"
            
            cmd = ["skopeo", "copy"]
            
            if insecure_policy:
                cmd.append("--insecure-policy")
                
            if skip_tls_verify:
                cmd.append("--dest-tls-verify=false")
            
            if dest_username and dest_password:
                cmd.extend([
                    "--dest-creds", 
                    f"{dest_username}:{dest_password}"
                ])
            
            cmd.extend([
                f"docker://{image_url}",
                f"docker://{dest_image_url}"
            ])
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )
            
            results.append({
                "image": image_url,
                "status": "success",
                "destination": dest_image_url
            })
            
        except Exception as e:
            results.append({
                "image": image_url,
                "status": "error",
                "error": str(e)
            })
    
    return jsonify({
        "batch_results": results,
        "total": len(image_list),
        "successful": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] == "error")
    })

@app.route('/health')
@limiter.exempt
def health_check():
    """Healthcheck endpoint for container orchestrators"""
    return jsonify({"status": "healthy"})

if __name__ == '__main__':
    # In production, debug should be False
    debug_mode = os.environ.get('FLASK_ENV', 'debug') != 'production'
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 8008))
    app.run(host=host, port=port, debug=debug_mode)