import os
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from app.extensions import mysql
from app.auth.decorators import (
    get_current_user,
    login_required,
    role_required,
)
from app.pharmacy import pharmacy_bp


# ============================================================
# PHARMACY CONFIGURATION
# ============================================================

ALLOWED_PRESCRIPTION_EXTENSIONS = {
    "pdf", "png", "jpg", "jpeg", "webp"
}
MAX_PRESCRIPTION_SIZE = 4 * 1024 * 1024
CART_SESSION_KEY = "medivera_pharmacy_cart"


# ============================================================
# DATABASE HELPERS
# ============================================================

def fetch_one(query, params=()):
    cursor = mysql.connection.cursor()
    try:
        cursor.execute(query, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def fetch_all(query, params=()):
    cursor = mysql.connection.cursor()
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def execute_query(query, params=()):
    cursor = mysql.connection.cursor()
    try:
        cursor.execute(query, params)
        mysql.connection.commit()
        return cursor.lastrowid
    except Exception:
        mysql.connection.rollback()
        raise
    finally:
        cursor.close()


def current_patient():
    user = get_current_user()

    if not user or not user.get("patient_id"):
        return None

    return user


def get_patient_admission(patient_id):
    return fetch_one(
        """
        SELECT
            a.admission_id,
            a.room_id,
            a.bed_id,
            r.room_number,
            b.bed_number
        FROM admissions a
        INNER JOIN rooms r
            ON r.room_id = a.room_id
        LEFT JOIN beds b
            ON b.bed_id = a.bed_id
        WHERE a.patient_id = %s
          AND a.admission_status = 'admitted'
          AND a.discharge_date IS NULL
        ORDER BY a.admission_date DESC
        LIMIT 1
        """,
        (patient_id,)
    )


def get_cart():
    cart = session.get(CART_SESSION_KEY, {})
    return cart if isinstance(cart, dict) else {}


def save_cart(cart):
    session[CART_SESSION_KEY] = cart
    session.modified = True


def get_cart_details():
    cart = get_cart()
    items = []
    subtotal = Decimal("0.00")

    for inventory_id, quantity in cart.items():
        try:
            inventory_id = int(inventory_id)
            quantity = int(quantity)
        except (TypeError, ValueError):
            continue

        if quantity < 1:
            continue

        medicine = fetch_one(
            """
            SELECT
                inventory_id,
                item_name,
                unit,
                unit_price,
                quantity AS stock_quantity,
                expiry_date,
                is_active
            FROM inventory
            WHERE inventory_id = %s
              AND item_category = 'Medicine'
              AND is_active = 1
              AND (expiry_date IS NULL OR expiry_date >= %s)
            LIMIT 1
            """,
            (inventory_id, date.today())
        )

        if not medicine:
            continue

        price = Decimal(str(medicine["unit_price"]))
        line_total = price * quantity
        subtotal += line_total

        items.append({
            "inventory_id": inventory_id,
            "item_name": medicine["item_name"],
            "unit": medicine["unit"],
            "unit_price": price,
            "stock_quantity": medicine["stock_quantity"],
            "quantity": quantity,
            "line_total": line_total,
        })

    return items, subtotal


# ============================================================
# MEDICINE STORE
# ============================================================

@pharmacy_bp.route("/")
@login_required
@role_required("patient")
def store():
    user = current_patient()

    if not user:
        flash(
            "Your account is not linked to a patient record.",
            "warning"
        )
        return redirect(url_for("patient.dashboard"))

    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    query = """
        SELECT
            inventory_id,
            item_name,
            item_category,
            quantity,
            unit,
            reorder_level,
            unit_price,
            expiry_date,
            stock_status,
            medicine_image,
            generic_name,
            brand_name,
            strength,
            dosage_form,
            manufacturer,
            description,
            requires_prescription,
            storage_instructions
        FROM inventory
        WHERE item_category = 'Medicine'
          AND is_active = 1
          AND quantity > 0
          AND unit_price > 0
          AND (
                expiry_date IS NULL
                OR expiry_date >= %s
          )
    """

    params = [date.today()]

    if search:
        query += """
            AND (
                item_name LIKE %s
                OR generic_name LIKE %s
                OR brand_name LIKE %s
                OR manufacturer LIKE %s
            )
        """
        search_term = f"%{search}%"
        params.extend([search_term] * 4)

    if category:
        query += " AND dosage_form = %s"
        params.append(category)

    query += " ORDER BY item_name ASC"

    medicines = fetch_all(query, tuple(params))

    categories = fetch_all(
        """
        SELECT DISTINCT
            dosage_form AS item_category
        FROM inventory
        WHERE item_category = 'Medicine'
          AND is_active = 1
          AND dosage_form IS NOT NULL
          AND dosage_form <> ''
        ORDER BY dosage_form
        """
    )

    admission = get_patient_admission(user["patient_id"])
    cart = get_cart()

    return render_template(
        "pharmacy/store.html",
        medicines=medicines,
        categories=categories,
        search=search,
        selected_category=category,
        admission=admission,
        cart_count=sum(
            int(value)
            for value in cart.values()
            if str(value).isdigit()
        ),
    )


# ============================================================
# ADD TO CART
# ============================================================

@pharmacy_bp.route("/cart/add/<int:inventory_id>", methods=["POST"])
@login_required
@role_required("patient")
def add_to_cart(inventory_id):
    current_patient()

    try:
        quantity = int(request.form.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 0

    if quantity < 1 or quantity > 100:
        flash("Please enter a valid quantity.", "warning")
        return redirect(url_for("pharmacy.store"))

    medicine = fetch_one(
        """
        SELECT inventory_id, item_name, quantity, expiry_date, is_active
        FROM inventory
        WHERE inventory_id = %s
          AND item_category = 'Medicine'
          AND is_active = 1
        LIMIT 1
        """,
        (inventory_id,)
    )

    if not medicine:
        flash("This medicine is not available in the catalogue.", "danger")
        return redirect(url_for("pharmacy.store"))

    if medicine["expiry_date"] and medicine["expiry_date"] < date.today():
        flash("This medicine has expired and cannot be ordered.", "danger")
        return redirect(url_for("pharmacy.store"))

    cart = get_cart()
    existing_quantity = int(cart.get(str(inventory_id), 0))

    if existing_quantity + quantity > int(medicine["quantity"]):
        flash(
            f"Only {medicine['quantity']} units are currently in stock.",
            "warning"
        )
        return redirect(url_for("pharmacy.store"))

    cart[str(inventory_id)] = existing_quantity + quantity
    save_cart(cart)

    flash(f"{medicine['item_name']} added to your cart.", "success")
    return redirect(url_for("pharmacy.store"))


# ============================================================
# CART
# ============================================================

@pharmacy_bp.route("/cart")
@login_required
@role_required("patient")
def cart():
    current_patient()
    items, subtotal = get_cart_details()

    return render_template(
        "pharmacy/cart.html",
        items=items,
        subtotal=subtotal,
    )


@pharmacy_bp.route("/cart/update", methods=["POST"])
@login_required
@role_required("patient")
def update_cart():
    current_patient()

    cart = get_cart()
    updated_cart = {}

    for inventory_id in list(cart.keys()):
        try:
            quantity = int(
                request.form.get(f"quantity_{inventory_id}", 0)
            )
            inventory_number = int(inventory_id)
        except (TypeError, ValueError):
            continue

        if quantity <= 0:
            continue

        medicine = fetch_one(
            """
            SELECT quantity
            FROM inventory
            WHERE inventory_id = %s
              AND item_category = 'Medicine'
              AND is_active = 1
              AND (expiry_date IS NULL OR expiry_date >= %s)
            """,
            (inventory_number, date.today())
        )

        if not medicine:
            continue

        if quantity > int(medicine["quantity"]):
            flash(
                f"Requested quantity exceeds available stock for item "
                f"#{inventory_number}.",
                "warning"
            )
            quantity = int(medicine["quantity"])

        if quantity > 0:
            updated_cart[str(inventory_number)] = quantity

    save_cart(updated_cart)
    flash("Your cart has been updated.", "success")
    return redirect(url_for("pharmacy.cart"))


@pharmacy_bp.route("/cart/remove/<int:inventory_id>", methods=["POST"])
@login_required
@role_required("patient")
def remove_from_cart(inventory_id):
    current_patient()

    cart = get_cart()
    cart.pop(str(inventory_id), None)
    save_cart(cart)

    flash("Item removed from your cart.", "success")
    return redirect(url_for("pharmacy.cart"))


# ============================================================
# PRESCRIPTION UPLOAD
# ============================================================

@pharmacy_bp.route("/prescription/upload", methods=["POST"])
@login_required
@role_required("patient")
def upload_prescription():
    user = current_patient()

    if not user:
        flash("Your account is not linked to a patient record.", "danger")
        return redirect(url_for("pharmacy.store"))

    uploaded_file = request.files.get("prescription_file")

    if not uploaded_file or not uploaded_file.filename:
        flash("Please select a prescription file.", "warning")
        return redirect(url_for("pharmacy.checkout"))

    original_filename = secure_filename(uploaded_file.filename)

    if not original_filename or "." not in original_filename:
        flash("Invalid prescription filename.", "danger")
        return redirect(url_for("pharmacy.checkout"))

    extension = original_filename.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_PRESCRIPTION_EXTENSIONS:
        flash("Upload a PDF or supported image file.", "danger")
        return redirect(url_for("pharmacy.checkout"))

    file_data = uploaded_file.read(MAX_PRESCRIPTION_SIZE + 1)

    if not file_data or len(file_data) > MAX_PRESCRIPTION_SIZE:
        flash("Prescription must be a non-empty file up to 4 MB.", "danger")
        return redirect(url_for("pharmacy.checkout"))

    upload_folder = current_app.config.get("PATIENT_UPLOAD_FOLDER")

    if not upload_folder:
        current_app.logger.error("PATIENT_UPLOAD_FOLDER is not configured.")
        flash("Prescription upload is temporarily unavailable.", "danger")
        return redirect(url_for("pharmacy.checkout"))

    os.makedirs(upload_folder, exist_ok=True)

    stored_filename = f"{uuid.uuid4().hex}.{extension}"
    destination = os.path.join(upload_folder, stored_filename)

    try:
        with open(destination, "wb") as destination_file:
            destination_file.write(file_data)

        cursor = mysql.connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO patient_documents (
                    patient_id,
                    uploaded_by_user_id,
                    document_type,
                    original_filename,
                    stored_filename,
                    mime_type,
                    file_size
                )
                VALUES (%s, %s, 'prescription', %s, %s, %s, %s)
                """,
                (
                    user["patient_id"],
                    user["user_id"],
                    original_filename,
                    stored_filename,
                    uploaded_file.mimetype or "application/octet-stream",
                    len(file_data),
                )
            )
            mysql.connection.commit()
        finally:
            cursor.close()

    except Exception:
        mysql.connection.rollback()

        try:
            if os.path.exists(destination):
                os.remove(destination)
        except OSError:
            current_app.logger.exception(
                "Unable to remove an incomplete prescription upload."
            )

        current_app.logger.exception("Prescription upload failed.")
        flash("Unable to save the prescription. Please try again.", "danger")
        return redirect(url_for("pharmacy.checkout"))

    flash("Prescription uploaded successfully.", "success")
    return redirect(url_for("pharmacy.checkout"))


# ============================================================
# CHECKOUT
# ============================================================

@pharmacy_bp.route("/checkout", methods=["GET", "POST"])
@login_required
@role_required("patient")
def checkout():
    user = current_patient()

    if not user:
        flash("Your account is not linked to a patient record.", "danger")
        return redirect(url_for("pharmacy.store"))

    items, subtotal = get_cart_details()

    if not items:
        flash("Your cart is empty.", "info")
        return redirect(url_for("pharmacy.store"))

    admission = get_patient_admission(user["patient_id"])

    prescriptions = fetch_all(
        """
        SELECT
            document_id,
            original_filename,
            created_at
        FROM patient_documents
        WHERE patient_id = %s
          AND document_type = 'prescription'
        ORDER BY created_at DESC
        """,
        (user["patient_id"],)
    )

    if request.method == "GET":
        return render_template(
            "pharmacy/checkout.html",
            items=items,
            subtotal=subtotal,
            admission=admission,
            prescriptions=prescriptions,
        )

    delivery_method = request.form.get("delivery_method", "").strip()
    prescription_id = request.form.get(
        "prescription_document_id", ""
    ).strip()
    order_notes = request.form.get("order_notes", "").strip()

    if delivery_method not in ("Bedside", "Pharmacy Collection"):
        flash("Please select a valid delivery method.", "warning")
        return redirect(url_for("pharmacy.checkout"))

    if delivery_method == "Bedside" and not admission:
        flash(
            "Bedside delivery is available only during an active admission.",
            "warning"
        )
        return redirect(url_for("pharmacy.checkout"))

    if len(order_notes) > 2000:
        flash("Order notes are too long.", "warning")
        return redirect(url_for("pharmacy.checkout"))

    prescription_document_id = None

    if prescription_id:
        try:
            prescription_document_id = int(prescription_id)
        except ValueError:
            flash("Invalid prescription selection.", "warning")
            return redirect(url_for("pharmacy.checkout"))

        selected_document = fetch_one(
            """
            SELECT document_id
            FROM patient_documents
            WHERE document_id = %s
              AND patient_id = %s
              AND document_type = 'prescription'
            LIMIT 1
            """,
            (prescription_document_id, user["patient_id"])
        )

        if not selected_document:
            flash("The selected prescription is not valid.", "danger")
            return redirect(url_for("pharmacy.checkout"))

    delivery_location = None

    if delivery_method == "Bedside":
        delivery_location = f"Room {admission['room_number']}"

        if admission.get("bed_number"):
            delivery_location += f" | Bed {admission['bed_number']}"

    cart_snapshot = get_cart()
    cursor = mysql.connection.cursor()

    try:
        cursor.execute("START TRANSACTION")

        validated_items = []
        order_subtotal = Decimal("0.00")

        for inventory_id, quantity in cart_snapshot.items():
            try:
                inventory_number = int(inventory_id)
                requested_quantity = int(quantity)
            except (TypeError, ValueError):
                raise ValueError("Your cart contains an invalid item.")

            if requested_quantity < 1:
                raise ValueError("Your cart contains an invalid quantity.")

            cursor.execute(
                """
                SELECT
                    inventory_id,
                    item_name,
                    item_category,
                    quantity,
                    unit,
                    unit_price,
                    expiry_date,
                    is_active
                FROM inventory
                WHERE inventory_id = %s
                FOR UPDATE
                """,
                (inventory_number,)
            )
            medicine = cursor.fetchone()

            if (
                not medicine
                or medicine["item_category"] != "Medicine"
                or not medicine["is_active"]
                or Decimal(str(medicine["unit_price"])) <= 0
            ):
                raise ValueError(
                    "A medicine in your cart is inactive or unavailable."
                )

            if (
                medicine["expiry_date"]
                and medicine["expiry_date"] < date.today()
            ):
                raise ValueError(
                    f"{medicine['item_name']} has expired."
                )

            if requested_quantity > int(medicine["quantity"]):
                raise ValueError(
                    f"Insufficient stock for {medicine['item_name']}. "
                    f"Available quantity: {medicine['quantity']}."
                )

            unit_price = Decimal(str(medicine["unit_price"]))
            line_total = unit_price * requested_quantity
            order_subtotal += line_total

            validated_items.append({
                "inventory_id": medicine["inventory_id"],
                "medicine_name": medicine["item_name"],
                "quantity": requested_quantity,
                "unit_price": unit_price,
                "line_total": line_total,
            })

        cursor.execute(
            """
            INSERT INTO pharmacy_orders (
                patient_id,
                ordered_by_user_id,
                prescription_document_id,
                order_status,
                delivery_method,
                delivery_location,
                order_notes,
                subtotal
            )
            VALUES (%s, %s, %s, 'Requested', %s, %s, %s, %s)
            """,
            (
                user["patient_id"],
                user["user_id"],
                prescription_document_id,
                delivery_method,
                delivery_location,
                order_notes or None,
                order_subtotal,
            )
        )

        order_id = cursor.lastrowid

        for item in validated_items:
            cursor.execute(
                """
                INSERT INTO pharmacy_order_items (
                    pharmacy_order_id,
                    inventory_id,
                    medicine_name,
                    quantity,
                    unit_price,
                    line_total
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    order_id,
                    item["inventory_id"],
                    item["medicine_name"],
                    item["quantity"],
                    item["unit_price"],
                    item["line_total"],
                )
            )

        # IMPORTANT:
        # Stock is intentionally NOT deducted here.
        # Pharmacy staff will validate and dispense the order later.

        mysql.connection.commit()
        session.pop(CART_SESSION_KEY, None)

        flash(
            f"Your pharmacy order #{order_id} has been submitted.",
            "success"
        )
        return redirect(
            url_for("pharmacy.order_detail", order_id=order_id)
        )

    except (ValueError, InvalidOperation) as error:
        mysql.connection.rollback()
        flash(str(error), "warning")
        return redirect(url_for("pharmacy.checkout"))

    except Exception:
        mysql.connection.rollback()
        current_app.logger.exception("Pharmacy checkout failed.")
        flash("Unable to place your order. Please try again.", "danger")
        return redirect(url_for("pharmacy.checkout"))

    finally:
        cursor.close()


# ============================================================
# PATIENT ORDER HISTORY
# ============================================================

@pharmacy_bp.route("/orders")
@login_required
@role_required("patient")
def orders():
    user = current_patient()

    if not user:
        flash("Your account is not linked to a patient record.", "danger")
        return redirect(url_for("pharmacy.store"))

    patient_orders = fetch_all(
        """
        SELECT
            pharmacy_order_id,
            order_status,
            delivery_method,
            delivery_location,
            subtotal,
            ordered_at,
            updated_at
        FROM pharmacy_orders
        WHERE patient_id = %s
        ORDER BY ordered_at DESC
        """,
        (user["patient_id"],)
    )

    return render_template(
        "pharmacy/orders.html",
        orders=patient_orders,
    )


@pharmacy_bp.route("/orders/<int:order_id>")
@login_required
@role_required("patient")
def order_detail(order_id):
    user = current_patient()

    if not user:
        flash("Your account is not linked to a patient record.", "danger")
        return redirect(url_for("pharmacy.store"))

    order = fetch_one(
        """
        SELECT
            pharmacy_order_id,
            order_status,
            delivery_method,
            delivery_location,
            order_notes,
            subtotal,
            ordered_at,
            updated_at,
            prescription_document_id
        FROM pharmacy_orders
        WHERE pharmacy_order_id = %s
          AND patient_id = %s
        LIMIT 1
        """,
        (order_id, user["patient_id"])
    )

    if not order:
        flash("Order not found.", "warning")
        return redirect(url_for("pharmacy.orders"))

    order_items = fetch_all(
        """
        SELECT
            medicine_name,
            quantity,
            unit_price,
            line_total
        FROM pharmacy_order_items
        WHERE pharmacy_order_id = %s
        ORDER BY order_item_id
        """,
        (order_id,)
    )

    return render_template(
        "pharmacy/order_detail.html",
        order=order,
        items=order_items,
    )
