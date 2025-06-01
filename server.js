const express = require('express');
const multer = require('multer');
const nodemailer = require('nodemailer');
const fs = require('fs');
const path = require('path');
const moment = require('moment');

const app = express();
const PORT = 3000;

// Configuration
const EMAIL_CONFIG = {
    service: 'gmail',
    auth: {
        user: 'esp32cambot.project@gmail.com',        // Your Gmail address
        pass: 'auifdtdgpgwovrjp'            // Gmail App Password (not regular password)
    }
};

const NOTIFICATION_EMAIL = 'esp32cambot.project@gmail.com'; // Where to send motion alerts

// Create uploads directory if it doesn't exist
const uploadsDir = path.join(__dirname, 'uploads');
if (!fs.existsSync(uploadsDir)) {
    fs.mkdirSync(uploadsDir);
}

// Configure multer for handling image uploads
const storage = multer.diskStorage({
    destination: (req, file, cb) => {
        cb(null, uploadsDir);
    },
    filename: (req, file, cb) => {
        const timestamp = moment().format('YYYY-MM-DD_HH-mm-ss');
        cb(null, `motion_${timestamp}.jpg`);
    }
});

const upload = multer({ 
    storage: storage,
    limits: {
        fileSize: 5 * 1024 * 1024 // 5MB limit
    }
});

// Configure nodemailer
const transporter = nodemailer.createTransport({
    service: EMAIL_CONFIG.service,
    auth: EMAIL_CONFIG.auth
});

// Verify email configuration on startup
transporter.verify((error, success) => {
    if (error) {
        console.error('Email configuration error:', error);
    } else {
        console.log('Email server is ready to send messages');
    }
});

// Middleware
app.use(express.json());
app.use(express.static('public'));

// Store recent motion events
let motionEvents = [];

// Routes
app.get('/', (req, res) => {
    res.send(`
        <html>
        <head>
            <title>ESP32-CAM Motion Detection Server</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 50px; }
                .container { max-width: 800px; margin: 0 auto; }
                .event { border: 1px solid #ddd; margin: 10px 0; padding: 15px; border-radius: 5px; }
                .timestamp { color: #666; font-size: 0.9em; }
                img { max-width: 300px; border-radius: 5px; margin: 10px 0; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>ESP32-CAM Motion Detection Server</h1>
                <p>Server Status: <strong>Running</strong></p>
                <p>Total Motion Events: <strong>${motionEvents.length}</strong></p>
                
                <h2>Recent Motion Events</h2>
                ${motionEvents.slice(-10).reverse().map(event => `
                    <div class="event">
                        <div class="timestamp">${event.timestamp}</div>
                        <p><strong>Motion detected and image captured</strong></p>
                        <p>Email sent: ${event.emailSent ? 'Yes' : 'Failed'}</p>
                        <img src="/uploads/${event.filename}" alt="Motion capture">
                    </div>
                `).join('')}
            </div>
        </body>
        </html>
    `);
});

// Handle image upload from ESP32-CAM
app.post('/upload', express.raw({ type: 'image/jpeg', limit: '5mb' }), (req, res) => {
  console.log('Receiving image from ESP32-CAM...');
  
  const timestamp = moment().format('YYYY-MM-DD_HH-mm-ss');
  const filename = `motion_${timestamp}.jpg`;
  const filepath = path.join(uploadsDir, filename);
  
  fs.writeFile(filepath, req.body, (err) => {
    if (err) {
      console.error('Error saving image:', err);
      return res.status(500).json({ error: 'Failed to save image' });
    }
    
    console.log(`Image saved: ${filename}`);
    
    // gửi email ...
    sendMotionAlert(filename, filepath)
      .then(() => {
        // ghi lại event ...
        motionEvents.push({ timestamp: moment().format('YYYY-MM-DD HH:mm:ss'), filename, emailSent: true });
        res.json({ success: true, message: 'Image saved and email sent', filename });
      })
      .catch(emailError => {
        motionEvents.push({ timestamp: moment().format('YYYY-MM-DD HH:mm:ss'), filename, emailSent: false });
        res.json({ success: true, message: 'Image saved but email failed', filename, emailError: emailError.message });
      });
  });
});

// Serve uploaded images
app.use('/uploads', express.static(uploadsDir));

// Get motion events API
app.get('/api/events', (req, res) => {
    res.json({
        totalEvents: motionEvents.length,
        recentEvents: motionEvents.slice(-20).reverse()
    });
});

// Function to send motion alert email
async function sendMotionAlert(filename, imagePath) {
    const timestamp = moment().format('YYYY-MM-DD HH:mm:ss');
    
    const mailOptions = {
        from: EMAIL_CONFIG.auth.user,
        to: NOTIFICATION_EMAIL,
        subject: `🚨 Motion Detected - ${timestamp}`,
        html: `
            <div style="font-family: Arial, sans-serif;">
                <h2 style="color: #d32f2f;">Motion Alert</h2>
                <p><strong>Time:</strong> ${timestamp}</p>
                <p><strong>Location:</strong> ESP32-CAM Security System</p>
                <p>Motion has been detected and captured by your security camera.</p>
                <hr>
                <p><em>Image is attached to this email.</em></p>
            </div>
        `,
        attachments: [
            {
                filename: filename,
                path: imagePath,
                contentType: 'image/jpeg'
            }
        ]
    };
    
    return transporter.sendMail(mailOptions);
}

// Health check endpoint
app.get('/health', (req, res) => {
    res.json({
        status: 'healthy',
        uptime: process.uptime(),
        timestamp: moment().format('YYYY-MM-DD HH:mm:ss'),
        totalEvents: motionEvents.length
    });
});

app.get('/', (req, res) => {
    res.send(`
        <html>
        <head>
            <title>ESP32-CAM Motion Detection Server</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 50px; }
                .container { max-width: 900px; margin: 0 auto; }
                .event { border: 1px solid #ddd; margin: 10px 0; padding: 15px; border-radius: 5px; position: relative; }
                .timestamp { color: #666; font-size: 0.9em; }
                img { max-width: 300px; border-radius: 5px; margin: 10px 0; display: block; }
                button.delete-btn {
                    position: absolute;
                    top: 10px;
                    right: 10px;
                    background: #e74c3c;
                    color: white;
                    border: none;
                    padding: 5px 10px;
                    border-radius: 3px;
                    cursor: pointer;
                }
                button.delete-btn:hover {
                    background: #c0392b;
                }
                h2 { margin-top: 40px; }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>ESP32-CAM Motion Detection Server</h1>
                <p>Server Status: <strong>Running</strong></p>
                <p>Total Motion Events: <strong>${motionEvents.length}</strong></p>
                
                <h2>Recent Motion Events</h2>
                ${motionEvents.slice(-10).reverse().map(event => `
                    <div class="event" data-filename="${event.filename}">
                        <button class="delete-btn">Xóa</button>
                        <div class="timestamp">${event.timestamp}</div>
                        <p><strong>Motion detected and image captured</strong></p>
                        <p>Email sent: ${event.emailSent ? 'Yes' : 'Failed'}</p>
                        <img src="/uploads/${event.filename}" alt="Motion capture">
                    </div>
                `).join('')}

                <h2>Live Stream from ESP32-CAM</h2>
                <img src="http://192.168.2.154:81/stream" alt="ESP32-CAM Live Stream" style="max-width:100%; border: 1px solid #ccc; border-radius:5px;">
            </div>

            <script>
                document.querySelectorAll('.delete-btn').forEach(button => {
                    button.addEventListener('click', () => {
                        if (!confirm('DELETE?')) return;

                        const eventDiv = button.closest('.event');
                        const filename = eventDiv.getAttribute('data-filename');

                        fetch('/api/delete/' + encodeURIComponent(filename), {
                            method: 'DELETE'
                        })
                        .then(res => res.json())
                        .then(data => {
                            if (data.success) {
                                eventDiv.remove();
                                alert('CTRL + ALT + DEL!');
                            } else {
                                alert('ERROR DEL: ' + data.message);
                            }
                        })
                        .catch(err => {
                            alert('ERROR DEL: ' + err.message);
                        });
                    });
                });
            </script>
        </body>
        </html>
    `);
});

app.delete('/api/delete/:filename', (req, res) => {
  const filename = req.params.filename;
  const filepath = path.join(uploadsDir, filename);

  fs.unlink(filepath, (err) => {
    if (err) {
      console.error('Failed to delete file:', err);
      return res.status(500).json({ success: false, message: 'Failed to delete file' });
    }

    // Xóa event tương ứng trong mảng
    motionEvents = motionEvents.filter(event => event.filename !== filename);

    res.json({ success: true, message: 'File deleted' });
  });
});
// Start server
app.listen(PORT, () => {
    console.log(`Server running on port ${PORT}`);
    console.log(`View dashboard at: http://localhost:${PORT}`);
    console.log(`Upload endpoint: http://localhost:${PORT}/upload`);
});

// Graceful shutdown
process.on('SIGINT', () => {
    console.log('\nShutting down server...');
    process.exit(0);
});