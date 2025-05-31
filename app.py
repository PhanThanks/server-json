from flask import Flask, request, jsonify, render_template_string
import torch
import cv2
import numpy as np
import base64
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import io
import os
from datetime import datetime
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Email configuration
EMAIL_SENDER = "esp32cambot.project@gmail.com"
EMAIL_PASSWORD = "auifdtdgpgwovrjp"
EMAIL_RECIPIENT = "esp32cambot.project@gmail.com"
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Load YOLOv5 model
try:
    # Use the official YOLOv5s model pre-trained on COCO dataset
    model = torch.hub.load('ultralytics/yolov5', 'yolov5s', pretrained=True)
    model.eval()
    print("YOLOv5 model loaded successfully!")
except Exception as e:
    print(f"Error loading model: {e}")
    model = None

def send_warning_email(image_data, detections):
    """Send warning email with detected person image"""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = EMAIL_RECIPIENT
        msg['Subject'] = "🚨 PERSON DETECTED - Security Alert"
        
        # Create HTML email body
        html_body = f"""
        <html>
            <body>
                <h2 style="color: red;">🚨 PERSON DETECTED!</h2>
                <p><strong>Alert Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p><strong>Detection Details:</strong></p>
                <ul>
                    <li>Number of persons detected: {len([d for d in detections if d['class'] == 'person'])}</li>
                    <li>Confidence levels: {[f"{d['confidence']:.2f}" for d in detections if d['class'] == 'person']}</li>
                </ul>
                <p>Please find the captured image with detection boxes attached to this email.</p>
                <hr>
                <p><em>ESP32-CAM Security System with AI Person Detection</em></p>
            </body>
        </html>
        """
        
        msg.attach(MIMEText(html_body, 'html'))
        
        # Attach processed image
        img_part = MIMEBase('application', 'octet-stream')
        img_part.set_payload(image_data)
        encoders.encode_base64(img_part)
        img_part.add_header(
            'Content-Disposition',
            f'attachment; filename="person_detected_{datetime.now().strftime("%Y%m%d_%H%M%S")}.jpg"'
        )
        msg.attach(img_part)
        
        # Send email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        
        print("Warning email sent successfully!")
        return True
        
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def detect_persons(image):
    """Detect persons in image using YOLOv5"""
    if model is None:
        return [], image
    
    try:
        # Run inference
        results = model(image)
        
        # Process results
        detections = []
        annotated_image = image.copy()
        
        # Get predictions
        pred = results.pred[0]  # predictions for first image
        
        if len(pred) > 0:
            for detection in pred:
                x1, y1, x2, y2, conf, cls = detection.tolist()
                
                # Get class name
                class_name = model.names[int(cls)]
                
                # Only process person detections with confidence > 0.5
                if class_name == 'person' and conf > 0.5:
                    detections.append({
                        'class': class_name,
                        'confidence': conf,
                        'bbox': [int(x1), int(y1), int(x2), int(y2)]
                    })
                    
                    # Draw bounding box
                    cv2.rectangle(annotated_image, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    cv2.putText(annotated_image, f'Person {conf:.2f}', 
                              (int(x1), int(y1)-10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        
        return detections, annotated_image
        
    except Exception as e:
        print(f"Error in person detection: {e}")
        return [], image

@app.route('/')
def home():
    """Simple status page"""
    html = """
    <html>
        <head><title>ESP32-CAM YOLOv5 Person Detection Server</title></head>
        <body style="font-family: Arial, sans-serif; margin: 40px;">
            <h1>🤖 ESP32-CAM YOLOv5 Person Detection Server</h1>
            <p><strong>Status:</strong> <span style="color: green;">Running</span></p>
            <p><strong>Model:</strong> YOLOv5s (COCO dataset)</p>
            <p><strong>Detection Target:</strong> Persons with confidence > 0.5</p>
            
            <h2>API Endpoints:</h2>
            <ul>
                <li><code>POST /detect</code> - Send image for person detection</li>
                <li><code>GET /status</code> - Server status</li>
            </ul>
            
            <h2>Usage:</h2>
            <p>Send a POST request to <code>/detect</code> with image data in JSON format:</p>
            <pre style="background: #f4f4f4; padding: 10px;">
{
    "image": "base64_encoded_image_data"
}
            </pre>
            
            <h2>Response:</h2>
            <pre style="background: #f4f4f4; padding: 10px;">
{
    "person_detected": true/false,
    "detections": [...],
    "email_sent": true/false,
    "message": "..."
}
            </pre>
        </body>
    </html>
    """
    return html

@app.route('/status')
def status():
    """API status endpoint"""
    return jsonify({
        "status": "running",
        "model_loaded": model is not None,
        "timestamp": datetime.now().isoformat()
    })

@app.route('/detect', methods=['POST'])
def detect_person():
    """Main detection endpoint"""
    try:
        # Get image data from request
        data = request.get_json()
        
        if not data or 'image' not in data:
            return jsonify({"error": "No image data provided"}), 400
        
        # Decode base64 image
        try:
            image_data = base64.b64decode(data['image'])
            nparr = np.frombuffer(image_data, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if image is None:
                raise ValueError("Invalid image data")
                
        except Exception as e:
            return jsonify({"error": f"Invalid image format: {str(e)}"}), 400
        
        # Detect persons
        detections, annotated_image = detect_persons(image)
        
        person_detected = len(detections) > 0
        email_sent = False
        
        # Send warning email if person detected
        if person_detected:
            # Encode annotated image
            _, buffer = cv2.imencode('.jpg', annotated_image)
            annotated_image_data = buffer.tobytes()
            
            email_sent = send_warning_email(annotated_image_data, detections)
        
        response = {
            "person_detected": person_detected,
            "detections": detections,
            "email_sent": email_sent,
            "message": f"{'Person detected! Warning email sent.' if person_detected and email_sent else 'No person detected.' if not person_detected else 'Person detected but email failed.'}"
        }
        
        print(f"Detection result: {response}")
        return jsonify(response)
        
    except Exception as e:
        print(f"Error in detection endpoint: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)