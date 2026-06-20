from flask import Flask, render_template, request, send_file, redirect, url_for, session
from pymongo import MongoClient
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from PyPDF2 import PdfReader, PdfWriter
from io import BytesIO
import datetime
import base64
import os
import requests
from reportlab.lib.utils import ImageReader
from bson import ObjectId
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = "yoursecret"  # needed for session

# MongoDB connection
client = MongoClient("mongodb://localhost:27017/")
db = client["documents_db"]
collection = db["submissions"]

# Register handwriting font
font_path = "static/fonts/StoryScript-Regular.ttf"
pdfmetrics.registerFont(TTFont("Handwriting", font_path))

# Paystack keys (set via env variables in production)
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_live_")
PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY", "pk_test_xxx")


@app.route('/')
def index():
    return render_template("form.html")


# Step 1: Collect form and create pending record
@app.route('/submit', methods=['POST'])
def submit():
    data = {
        "student_name": request.form['student_name'],
        "student_id": request.form['student_id'],
        "kcse_index": request.form['kcse_index'],
        "phone": request.form['phone'],
        "email": request.form['email'],
        "university": request.form['university'],
        "admission": request.form['admission'],
        "parent_name": request.form['parent_name'],
        "parent_id": request.form['parent_id'],
        "parent_phone": request.form['parent_phone'],
        "relationship": request.form.get('relationship'),
        "marital_status": request.form.get('marital_status'),
        "signature": request.form.get('signature'),  # Base64 image
        "created_at": datetime.datetime.utcnow(),
        "payment_status": "pending"
    }
    inserted = collection.insert_one(data)
    session["submission_id"] = str(inserted.inserted_id)

    # Create Paystack transaction
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
    "email": data["email"],
    "amount": 100,
    "callback_url": url_for("verify_payment", submission_id=str(inserted.inserted_id), _external=True),
    "metadata": {"submission_id": str(inserted.inserted_id)}
}

    response = requests.post("https://api.paystack.co/transaction/initialize",
                             json=payload, headers=headers)
    res_data = response.json()

    if res_data.get("status") and res_data.get("data"):
        return redirect(res_data["data"]["authorization_url"])
    else:
        return "Payment initialization failed", 400

@app.route("/payment", methods=["POST"])
def payment():
    # Collect form data from request
    data = {
        "student_name": request.form['student_name'],
        "student_id": request.form['student_id'],
        "kcse_index": request.form['kcse_index'],
        "phone": request.form['phone'],
        "email": request.form['email'],
        "university": request.form['university'],
        "admission": request.form['admission'],
        "parent_name": request.form['parent_name'],
        "parent_id": request.form['parent_id'],
        "parent_phone": request.form['parent_phone'],
        "relationship": request.form.get('relationship'),
        "marital_status": request.form.get('marital_status'),
        "signature": request.form.get('signature'),   # Base64 image
        "created_at": datetime.datetime.utcnow(),
        "payment_status": "pending"
    }

    # Save to MongoDB first with pending status
    result = collection.insert_one(data)
    submission_id = str(result.inserted_id)

    # Create Paystack transaction
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "email": data["email"],
        "amount": 100,  # amount in kobo (100 = 1 NGN, adjust for KES equivalent)
        "callback_url": url_for("verify_payment", submission_id=submission_id, _external=True),
        "metadata": {"submission_id": submission_id}
    }

    response = requests.post("https://api.paystack.co/transaction/initialize",
                             json=payload, headers=headers)
    res_data = response.json()

    if res_data.get("status") and res_data.get("data"):
        return redirect(res_data["data"]["authorization_url"])
    else:
        print("Paystack error:", res_data)  # Debugging
        return f"Payment initialization failed: {res_data}", 400


# Step 2: Verify payment after Paystack redirect
@app.route('/verify_payment')
def verify_payment():
    reference = request.args.get("reference")
    submission_id = request.args.get("submission_id")

    if not reference or not submission_id:
        return "Invalid payment session", 400

    headers = {"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"}
    response = requests.get(f"https://api.paystack.co/transaction/verify/{reference}",
                            headers=headers)
    res_data = response.json()

    if res_data.get("status") and res_data["data"]["status"] == "success":
        # Update DB record
        collection.update_one({"_id": ObjectId(submission_id)},
                              {"$set": {"payment_status": "success",
                                        "payment_reference": reference}})
        data = collection.find_one({"_id": ObjectId(submission_id)})
        
        # Store submission_id in session for later download
        session['last_paid_id'] = submission_id
        
        # Redirect to success page
        return redirect(url_for('payment_success', submission_id=submission_id))
    else:
        return "Payment verification failed", 400

@app.route('/payment_success/<submission_id>')
def payment_success(submission_id):
    # Verify the payment is successful
    data = collection.find_one({"_id": ObjectId(submission_id)})
    if data and data.get("payment_status") == "success":
        return render_template("payment_success.html", submission_id=submission_id)
    else:
        return "Payment not found or not completed", 404

@app.route('/download_pdf/<submission_id>')
def download_pdf(submission_id):
    # Verify the payment is successful
    data = collection.find_one({"_id": ObjectId(submission_id)})
    if data and data.get("payment_status") == "success":
        return generate_pdf(data)
    else:
        return "Payment not verified. Cannot download PDF.", 403

@app.route("/already_paid", methods=["GET", "POST"])
def already_paid():
    if request.method == "POST":
        email = request.form.get("email")
        user_data = collection.find_one({"email": email, "payment_status": "success"})
        
        if user_data:
            submission_id = str(user_data["_id"])
            return redirect(url_for('payment_success', submission_id=submission_id))
        else:
            return render_template("already_paid.html", error="No record found or payment not completed.")
    
    return render_template("already_paid.html")


# PDF Generator
def generate_pdf(data):
    packet = BytesIO()
    can = canvas.Canvas(packet, pagesize=A4)
    bic_blue = HexColor("#0A1172")
    can.setFillColor(bic_blue)
    can.setFont("Handwriting", 14)

    # Student details
    can.drawString(290, 680, data["student_name"])
    can.drawString(190, 665, data["student_id"])
    can.drawString(190, 649, data["kcse_index"])
    can.drawString(190, 630, data["phone"])
    can.drawString(390, 630, data["email"])
    can.drawString(400, 665, data["university"])
    can.drawString(400, 649, data["admission"])
    can.drawString(300, 575, data["parent_name"])
    can.drawString(300, 560, data["parent_id"])
    can.drawString(300, 542, data["parent_phone"])

    # Ticks
    can.setFont("ZapfDingbats", 16)
    if data.get("relationship") == "Mother":
        can.drawString(280, 530, "✔")
    elif data.get("relationship") == "Father":
        can.drawString(354, 530, "✔")

    if data.get("marital_status") == "Single":
        can.drawString(280, 510, "✔")
    elif data.get("marital_status") == "Separated":
        can.drawString(354, 510, "✔")
    elif data.get("marital_status") == "Divorced":
        can.drawString(420, 510, "✔")

    # Parent signature
    can.setFont("Handwriting", 20)
    can.drawString(60, 300, data["parent_name"])
    if data.get("signature"):
        signature_data = data["signature"].split(",")[1]
        signature_bytes = base64.b64decode(signature_data)
        signature_img = ImageReader(BytesIO(signature_bytes))
        can.drawImage(signature_img, 250, 300, width=150, height=60, mask='auto')

    # Date
    today = datetime.datetime.now().strftime("%d/%m/%Y")
    can.setFont("Handwriting", 14)
    can.drawString(440, 300, today)

    can.save()
    packet.seek(0)

    # Merge with blank form
    template_path = "static/blank_form.pdf"
    template_pdf = PdfReader(open(template_path, "rb"))
    overlay_pdf = PdfReader(packet)

    writer = PdfWriter()
    page = template_pdf.pages[0]
    page.merge_page(overlay_pdf.pages[0])
    writer.add_page(page)

    output_buffer = BytesIO()
    writer.write(output_buffer)
    output_buffer.seek(0)

    return send_file(output_buffer, as_attachment=True,
                     download_name="singleparentcert.pdf",
                     mimetype="application/pdf")


if __name__ == '__main__':
    app.run(debug=True)