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

app = Flask(__name__)

# Configuration
YOLO_API_URL = os.environ.get('YOLO_API_URL')  # Your local YOLO API server
GMAIL_USER = os.environ.get('GMAIL_USER')
GMAIL_PASSWORD = os.environ.get('GMAIL_PASSWORD')
RECIPIENT_EMAIL = os.environ.get('RECIPIENT_EMAIL')

# Backup YOLO URLs in case primary fails
YOLO_BACKUP_URLS = [
    "http://127.0.0.1:5001/detect",
    "http://localhost:5001/detect"
]

@app.route('/')
def home():
    return "ESP32-CAM Motion Detection Server Running"

@app.route('/upload', methods=['POST'])
def upload_photo():
    try:
        # Get image data from ESP32-CAM
        image_data = request.get_data()
        
        if not image_data:
            return jsonify({"error": "No image data received"}), 400
        
        print(f"Received image of size: {len(image_data)} bytes at {datetime.now()}")
        
        # Send image to YOLO API for object detection
        yolo_result = send_to_yolo(image_data)
        
        if yolo_result:
            # Process YOLO results and create annotated image
            annotated_image = process_yolo_results(image_data, yolo_result)
            
            # Send email with results
            email_sent = send_email_notification(annotated_image, yolo_result)
            
            response_data = {
                "status": "success",
                "message": "Photo processed",
                "detections": yolo_result.get('detections', []),
                "detection_count": len(yolo_result.get('detections', [])),
                "email_sent": email_sent,
                "timestamp": datetime.now().isoformat()
            }
            
            if email_sent:
                response_data["message"] += " and email sent"
            else:
                response_data["message"] += " but email failed"
            
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
        print(f"Error processing upload: {str(e)}")
        return jsonify({"error": str(e)}), 500

def send_to_yolo(image_data):
    """Send image to YOLO API for object detection with fallback URLs"""
    urls_to_try = [YOLO_API_URL] + YOLO_BACKUP_URLS
    
    for url in urls_to_try:
        try:
            print(f"Trying YOLO API at: {url}")
            
            # Prepare the request to YOLO API
            files = {'image': ('image.jpg', image_data, 'image/jpeg')}
            
            response = requests.post(url, files=files, timeout=10)
            
            if response.status_code == 200:
                print(f"YOLO API success at: {url}")
                result = response.json()
                print(f"YOLO detected {len(result.get('detections', []))} objects")
                return result
            else:
                print(f"YOLO API error at {url}: {response.status_code}")
                
        except requests.exceptions.ConnectTimeout:
            print(f"Connection timeout to {url}")
            continue
        except requests.exceptions.ConnectionError:
            print(f"Connection error to {url}")
            continue
        except Exception as e:
            print(f"Error calling YOLO API at {url}: {str(e)}")
            continue
    
    print("All YOLO API endpoints failed")
    return None

def process_yolo_results(image_data, yolo_result):
    """Process YOLO results and create annotated image"""
    try:
        # Open image
        image = Image.open(io.BytesIO(image_data))
        draw = ImageDraw.Draw(image)
        
        # Try to load a font (fallback to default if not available)
        try:
            font = ImageFont.truetype("arial.ttf", 20)
        except:
            font = ImageFont.load_default()
        
        # Draw bounding boxes and labels
        detections = yolo_result.get('detections', [])
        
        for detection in detections:
            # Assuming YOLO API returns: class, confidence, bbox coordinates
            class_name = detection.get('class', 'Unknown')
            confidence = detection.get('confidence', 0)
            bbox = detection.get('bbox', [])  # [x1, y1, x2, y2]
            
            if len(bbox) == 4:
                x1, y1, x2, y2 = bbox
                
                # Draw bounding box
                draw.rectangle([x1, y1, x2, y2], outline="red", width=3)
                
                # Draw label
                label = f"{class_name}: {confidence:.2f}"
                draw.text((x1, y1-25), label, fill="red", font=font)
        
        # Save annotated image to bytes
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='JPEG')
        img_byte_arr = img_byte_arr.getvalue()
        
        return img_byte_arr
        
    except Exception as e:
        print(f"Error processing image: {str(e)}")
        return image_data  # Return original image if processing fails

def send_email_notification(image_data, yolo_result, yolo_failed=False):
    """Send email with detected objects and annotated image"""
    try:
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
            msg['Subject'] = f"Motion Detected ({detection_count} objects) - {timestamp}"
        
        # Create email body
        if yolo_failed:
            body = f"""
Motion detected by ESP32-CAM at {timestamp}

YOLO API Server appears to be offline or unreachable.
Object detection could not be performed.

The original image is attached for manual review.

YOLO Server Status: Offline/Unreachable
Server URL: {YOLO_API_URL}
"""
        else:
            detections = yolo_result.get('detections', [])
            detection_count = len(detections)
            
            body = f"""
Motion detected by ESP32-CAM at {timestamp}

Detection Summary:
• Objects detected: {detection_count}
• YOLO API Status: Online

"""
            
            if detection_count > 0:
                body += "🔍 Detected objects:\n"
                for i, detection in enumerate(detections, 1):
                    class_name = detection.get('class', 'Unknown')
                    confidence = detection.get('confidence', 0)
                    body += f"   {i}. {class_name} (Confidence: {confidence:.1%})\n"
            else:
                body += "No specific objects detected in this image.\n"
            
            body += "\nSee attached annotated image for details."
        
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach image
        img_attachment = MIMEImage(image_data)
        
        if yolo_failed:
            filename = f'motion_detected_{timestamp.replace(":", "-").replace(" ", "_")}.jpg'
        else:
            detection_count = len(yolo_result.get('detections', []))
            filename = f'detection_{detection_count}objects_{timestamp.replace(":", "-").replace(" ", "_")}.jpg'
        
        img_attachment.add_header('Content-Disposition', 'attachment', filename=filename)
        msg.attach(img_attachment)
        
        # Send email
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_PASSWORD)
        text = msg.as_string()
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, text)
        server.quit()
        
        print(f"Email sent successfully to {RECIPIENT_EMAIL}")
        return True
        
    except Exception as e:
        print(f"Error sending email: {str(e)}")
        return False

@app.route('/test-yolo')
def test_yolo():
    """Test endpoint to check YOLO API connectivity"""
    try:
        # Test each YOLO URL
        results = {}
        urls_to_test = [YOLO_API_URL] + YOLO_BACKUP_URLS
        
        for url in urls_to_test:
            try:
                response = requests.get(url.replace('/detect', '/health'), timeout=5)
                if response.status_code == 200:
                    results[url] = "Online"
                else:
                    results[url] = f"Error {response.status_code}"
            except:
                results[url] = "Offline"
        
        return jsonify({
            "yolo_servers": results,
            "primary_server": YOLO_API_URL,
            "timestamp": datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)