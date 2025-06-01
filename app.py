from flask import Flask, request, jsonify
import requests
import smtplib
import base64
import io
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from datetime import datetime
import os
from PIL import Image, ImageDraw, ImageFont
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration
YOLO_API_URL = os.environ.get('YOLO_API_URL')
GMAIL_USER = os.environ.get('GMAIL_USER')
GMAIL_PASSWORD = os.environ.get('GMAIL_PASSWORD')  # Use App Password for Gmail
RECIPIENT_EMAIL = os.environ.get('RECIPIENT_EMAIL')

# Backup YOLO URLs in case primary fails
YOLO_BACKUP_URLS = [
    "http://127.0.0.1:5001/detect",
    "http://localhost:5001/detect",
    "http://127.0.0.1:8000/detect"
]

# Detection confidence threshold
CONFIDENCE_THRESHOLD = float(os.environ.get('CONFIDENCE_THRESHOLD', '0.5'))

@app.route('/')
def home():
    return jsonify({
        "service": "ESP32-CAM Motion Detection Server",
        "status": "Running",
        "version": "2.0",
        "endpoints": {
            "/upload": "POST - Upload image for detection",
            "/test-yolo": "GET - Test YOLO API connectivity",
            "/health": "GET - Server health check"
        }
    })

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "yolo_url": YOLO_API_URL
    })

@app.route('/upload', methods=['POST'])
def upload_photo():
    """Main endpoint for ESP32-CAM image upload and processing"""
    try:
        # Get image data from ESP32-CAM
        if 'image' in request.files:
            # Handle multipart/form-data upload
            file = request.files['image']
            image_data = file.read()
        else:
            # Handle raw binary upload
            image_data = request.get_data()
        
        if not image_data:
            return jsonify({"error": "No image data received"}), 400
        
        logger.info(f"Received image of size: {len(image_data)} bytes at {datetime.now()}")
        
        # Validate image data
        try:
            test_image = Image.open(io.BytesIO(image_data))
            image_width, image_height = test_image.size
            logger.info(f"Image dimensions: {image_width}x{image_height}")
        except Exception as e:
            logger.error(f"Invalid image data: {str(e)}")
            return jsonify({"error": "Invalid image data"}), 400
        
        # Send image to YOLO API for object detection
        yolo_result = send_to_yolo(image_data)
        
        if yolo_result:
            # Filter detections by confidence threshold
            filtered_detections = filter_detections(yolo_result)
            yolo_result['detections'] = filtered_detections
            
            # Process YOLO results and create annotated image
            annotated_image = process_yolo_results(image_data, yolo_result)
            
            # Send email with results
            email_sent = send_email_notification(annotated_image, yolo_result)
            
            response_data = {
                "status": "success",
                "message": "Photo processed successfully",
                "detections": filtered_detections,
                "detection_count": len(filtered_detections),
                "total_detections": len(yolo_result.get('all_detections', [])),
                "confidence_threshold": CONFIDENCE_THRESHOLD,
                "email_sent": email_sent,
                "image_size": f"{image_width}x{image_height}",
                "timestamp": datetime.now().isoformat()
            }
            
            if email_sent:
                response_data["message"] += " and notification email sent"
            else:
                response_data["message"] += " but email notification failed"
            
            return jsonify(response_data)
        else:
            # Even if YOLO fails, still send the original image via email
            email_sent = send_email_notification(image_data, {"detections": []}, yolo_failed=True)
            
            return jsonify({
                "status": "partial_success",
                "message": "YOLO processing failed but email sent with original image",
                "detections": [],
                "email_sent": email_sent,
                "timestamp": datetime.now().isoformat()
            })
            
    except Exception as e:
        logger.error(f"Error processing upload: {str(e)}")
        return jsonify({"error": str(e)}), 500

def filter_detections(yolo_result):
    """Filter detections based on confidence threshold"""
    all_detections = yolo_result.get('detections', [])
    filtered = []
    
    for detection in all_detections:
        confidence = detection.get('confidence', 0)
        if confidence >= CONFIDENCE_THRESHOLD:
            filtered.append(detection)
    
    # Store all detections for reference
    yolo_result['all_detections'] = all_detections
    logger.info(f"Filtered {len(all_detections)} detections to {len(filtered)} above {CONFIDENCE_THRESHOLD} confidence")
    
    return filtered

def send_to_yolo(image_data):
    """Send image to YOLO API for object detection with fallback URLs"""
    urls_to_try = [url for url in [YOLO_API_URL] + YOLO_BACKUP_URLS if url]
    
    for url in urls_to_try:
        try:
            logger.info(f"Trying YOLO API at: {url}")
            
            # Prepare the request to YOLO API
            files = {'image': ('image.jpg', image_data, 'image/jpeg')}
            
            # Add timeout and proper headers
            headers = {'Accept': 'application/json'}
            response = requests.post(url, files=files, headers=headers, timeout=15)
            
            if response.status_code == 200:
                logger.info(f"YOLO API success at: {url}")
                result = response.json()
                detection_count = len(result.get('detections', []))
                logger.info(f"YOLO detected {detection_count} objects")
                return result
            else:
                logger.warning(f"YOLO API error at {url}: {response.status_code} - {response.text}")
                
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout connecting to {url}")
            continue
        except requests.exceptions.ConnectionError:
            logger.warning(f"Connection error to {url}")
            continue
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error to {url}: {str(e)}")
            continue
        except Exception as e:
            logger.error(f"Unexpected error calling YOLO API at {url}: {str(e)}")
            continue
    
    logger.error("All YOLO API endpoints failed")
    return None

def process_yolo_results(image_data, yolo_result):
    """Process YOLO results and create annotated image"""
    try:
        # Open image
        image = Image.open(io.BytesIO(image_data))
        draw = ImageDraw.Draw(image)
        
        # Try to load a font (fallback to default if not available)
        try:
            # Try different font paths for different systems
            font_paths = [
                "/System/Library/Fonts/Arial.ttf",  # macOS
                "/usr/share/fonts/truetype/arial.ttf",  # Linux
                "C:/Windows/Fonts/arial.ttf",  # Windows
                "arial.ttf"
            ]
            font = None
            for font_path in font_paths:
                try:
                    font = ImageFont.truetype(font_path, 20)
                    break
                except:
                    continue
            if not font:
                font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()
        
        # Color scheme for different classes
        colors = ["red", "blue", "green", "yellow", "purple", "orange", "cyan", "magenta"]
        class_colors = {}
        
        # Draw bounding boxes and labels
        detections = yolo_result.get('detections', [])
        
        for i, detection in enumerate(detections):
            # Extract detection information
            class_name = detection.get('class', 'Unknown')
            confidence = detection.get('confidence', 0)
            bbox = detection.get('bbox', [])  # Expected format: [x1, y1, x2, y2]
            
            # Assign color to class
            if class_name not in class_colors:
                class_colors[class_name] = colors[len(class_colors) % len(colors)]
            color = class_colors[class_name]
            
            if len(bbox) == 4:
                x1, y1, x2, y2 = map(int, bbox)
                
                # Ensure coordinates are within image bounds
                img_width, img_height = image.size
                x1 = max(0, min(x1, img_width))
                y1 = max(0, min(y1, img_height))
                x2 = max(0, min(x2, img_width))
                y2 = max(0, min(y2, img_height))
                
                # Draw bounding box
                draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
                
                # Draw label with background
                label = f"{class_name}: {confidence:.1%}"
                
                # Get text size for background rectangle
                try:
                    bbox_text = draw.textbbox((0, 0), label, font=font)
                    text_width = bbox_text[2] - bbox_text[0]
                    text_height = bbox_text[3] - bbox_text[1]
                except:
                    # Fallback for older Pillow versions
                    text_width, text_height = draw.textsize(label, font=font)
                
                # Draw background rectangle for text
                text_y = max(0, y1 - text_height - 5)
                draw.rectangle([x1, text_y, x1 + text_width + 10, text_y + text_height + 5], 
                             fill=color)
                
                # Draw text
                draw.text((x1 + 5, text_y + 2), label, fill="white", font=font)
        
        # Add timestamp and summary
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        summary = f"Detected: {len(detections)} objects | {timestamp}"
        
        # Draw summary at bottom of image
        img_width, img_height = image.size
        try:
            bbox_summary = draw.textbbox((0, 0), summary, font=font)
            summary_width = bbox_summary[2] - bbox_summary[0]
            summary_height = bbox_summary[3] - bbox_summary[1]
        except:
            summary_width, summary_height = draw.textsize(summary, font=font)
        
        summary_y = img_height - summary_height - 10
        draw.rectangle([10, summary_y - 5, 10 + summary_width + 10, summary_y + summary_height + 5], 
                      fill="black")
        draw.text((15, summary_y), summary, fill="white", font=font)
        
        # Save annotated image to bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG', quality=95)
        img_byte_arr = img_byte_arr.getvalue()
        
        logger.info(f"Image annotated with {len(detections)} detections")
        return img_byte_arr
        
    except Exception as e:
        logger.error(f"Error processing image: {str(e)}")
        return image_data  # Return original image if processing fails

def send_email_notification(image_data, yolo_result, yolo_failed=False):
    """Send email with detected objects and annotated image"""
    try:
        # Validate email configuration
        if not all([GMAIL_USER, GMAIL_PASSWORD, RECIPIENT_EMAIL]):
            logger.error("Email configuration incomplete")
            return False
        
        # Create message
        msg = MIMEMultipart()
        msg['From'] = GMAIL_USER
        msg['To'] = RECIPIENT_EMAIL
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if yolo_failed:
            msg['Subject'] = f"Motion Detected (YOLO Offline) - {timestamp}"
        else:
            detections = yolo_result.get('detections', [])
            detection_count = len(detections)
            if detection_count > 0:
                msg['Subject'] = f"Objects Detected ({detection_count} found) - {timestamp}"
            else:
                msg['Subject'] = f"Motion Detected (No objects) - {timestamp}"
        
        # Create email body
        if yolo_failed:
            body = f"""
MOTION DETECTION ALERT

Timestamp: {timestamp}
ESP32-CAM Status: Active
YOLO API Status: Offline/Unreachable

Object detection could not be performed due to YOLO server issues.
The original image is attached for manual review.

Technical Details:
• Primary YOLO Server: {YOLO_API_URL}
• Server Status: Offline/Unreachable
• Image Processing: Bypassed

Please check the YOLO server configuration.
"""
        else:
            detections = yolo_result.get('detections', [])
            detection_count = len(detections)
            total_detections = len(yolo_result.get('all_detections', detections))
            
            body = f"""
OBJECT DETECTION REPORT

Timestamp: {timestamp}
ESP32-CAM Status: Active
YOLO API Status: Online

DETECTION SUMMARY:
• High-confidence objects: {detection_count}
• Total detections: {total_detections}
• Confidence threshold: {CONFIDENCE_THRESHOLD:.1%}

"""
            
            if detection_count > 0:
                body += "DETECTED OBJECTS:\n"
                for i, detection in enumerate(detections, 1):
                    class_name = detection.get('class', 'Unknown')
                    confidence = detection.get('confidence', 0)
                    body += f"   {i}. {class_name.title()} (Confidence: {confidence:.1%})\n"
            else:
                if total_detections > 0:
                    body += f"{total_detections} objects detected but below {CONFIDENCE_THRESHOLD:.1%} confidence threshold.\n"
                else:
                    body += "No objects detected in this image.\n"
            
            body += f"\nSee attached annotated image for visual details."
            
            # Add low-confidence detections if any
            low_conf_detections = [d for d in yolo_result.get('all_detections', []) 
                                 if d.get('confidence', 0) < CONFIDENCE_THRESHOLD]
            if low_conf_detections:
                body += f"\n\nLOW-CONFIDENCE DETECTIONS ({len(low_conf_detections)}):\n"
                for detection in low_conf_detections[:5]:  # Limit to 5
                    class_name = detection.get('class', 'Unknown')
                    confidence = detection.get('confidence', 0)
                    body += f"   • {class_name.title()} ({confidence:.1%})\n"
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach image
        img_attachment = MIMEImage(image_data)
        
        if yolo_failed:
            filename = f'motion_raw_{timestamp.replace(":", "-").replace(" ", "_")}.jpg'
        else:
            detection_count = len(yolo_result.get('detections', []))
            filename = f'detection_{detection_count}obj_{timestamp.replace(":", "-").replace(" ", "_")}.jpg'
        
        img_attachment.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(img_attachment)
        
        # Send email with better error handling
        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            text = msg.as_string()
            server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, text)
            server.quit()
            
            logger.info(f"Email sent successfully to {RECIPIENT_EMAIL}")
            return True
            
        except smtplib.SMTPAuthenticationError:
            logger.error("Gmail authentication failed. Check username/password or use App Password")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {str(e)}")
            return False
        
    except Exception as e:
        logger.error(f"Error sending email: {str(e)}")
        return False

@app.route('/test-yolo')
def test_yolo():
    """Test endpoint to check YOLO API connectivity"""
    try:
        # Test each YOLO URL
        results = {}
        urls_to_test = [url for url in [YOLO_API_URL] + YOLO_BACKUP_URLS if url]
        
        for url in urls_to_test:
            try:
                # Try health endpoint first
                health_url = url.replace('/detect', '/health')
                response = requests.get(health_url, timeout=5)
                if response.status_code == 200:
                    results[url] = {"status": "Online", "response": response.json()}
                else:
                    results[url] = {"status": f"Error {response.status_code}", "response": None}
            except requests.exceptions.ConnectionError:
                results[url] = {"status": "Connection Failed", "response": None}
            except requests.exceptions.Timeout:
                results[url] = {"status": "Timeout", "response": None}
            except Exception as e:
                results[url] = {"status": f"Error: {str(e)}", "response": None}
        
        return jsonify({
            "yolo_servers": results,
            "primary_server": YOLO_API_URL,
            "confidence_threshold": CONFIDENCE_THRESHOLD,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route('/config')
def show_config():
    """Show current configuration (without sensitive data)"""
    return jsonify({
        "yolo_api_url": YOLO_API_URL,
        "backup_urls": YOLO_BACKUP_URLS,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "gmail_configured": bool(GMAIL_USER and GMAIL_PASSWORD),
        "recipient_configured": bool(RECIPIENT_EMAIL),
        "timestamp": datetime.now().isoformat()
    })

if __name__ == '__main__':
    # Validate configuration
    if not GMAIL_USER or not GMAIL_PASSWORD or not RECIPIENT_EMAIL:
        logger.warning("Email configuration incomplete. Email notifications will fail.")
    
    if not YOLO_API_URL:
        logger.warning("YOLO_API_URL not configured. Using default localhost:5001")
    
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting ESP32-CAM Detection Server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)