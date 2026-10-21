# Hotel Management SaaS - Data Model

## Overview
This document defines the data model for a multi-tenant hotel management SaaS platform. The model is organized into three priority groups for phased implementation.

## Implementation Status
✅ **Priority 1**: Core Foundation (MVP) - COMPLETED
✅ **Priority 2**: Operational Essentials - PARTIALLY COMPLETED  
✅ **Priority 3**: Enhanced Operations - COMPLETED

## Multi-Tenancy Architecture
- **Tenant Model**: `Account` (organization/hotel chain)
- **Scoping**: All models include `account_id` and use `acts_as_tenant(:account)`
- **Isolation**: Data is isolated per account via subdomain-based routing

---

## Priority 1: Core Foundation (MVP) ✅

### Account (Enhanced) ✅
Top-level tenant model representing an organization or hotel chain.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| name | string | required | Organization name |
| subdomain | string | required, unique | Subdomain for tenant isolation |
| address | string | | Main office address |
| city | string | | City |
| state | string | | State/Province |
| country | string | | Country code (ISO) |
| postal_code | string | | Postal/ZIP code |
| phone | string | | Contact phone |
| email | string | | Contact email |
| timezone | string | default: 'UTC' | Default timezone |
| currency | string | default: 'USD' | Default currency (ISO) |
| status | string | default: 'active' | active/suspended/cancelled |
| trial_ends_at | datetime | | Trial period end date |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- has_many :hotels
- has_many :account_users
- has_many :users, through: :account_users

---

### Hotel (Enhanced) ✅
Individual hotel property within an account.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account |
| name | string | required | Hotel name |
| subdomain | string | unique(scope: account) | Hotel-specific subdomain |
| address | string | required | Street address |
| city | string | required | City |
| state | string | required | State/Province |
| country | string | required | Country code (ISO) |
| postal_code | string | required | Postal/ZIP code |
| phone | string | required | Contact phone |
| email | string | required | Contact email |
| website | string | | Hotel website |
| description | text | | Hotel description |
| check_in_time | time | default: '15:00' | Standard check-in time |
| check_out_time | time | default: '11:00' | Standard check-out time |
| timezone | string | required | Hotel timezone |
| star_rating | decimal | | Hotel star rating (1.0-5.0) |
| total_rooms | integer | | Total number of rooms |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- has_many :room_types
- has_many :rooms
- has_many :reservations

**Indexes:**
- index on account_id
- unique index on [account_id, subdomain]

---

### RoomType ✅
Categories of rooms available in a hotel (e.g., Standard, Deluxe, Suite).

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| name | string | required | Room type name |
| code | string | required | Internal code (e.g., 'STD', 'DLX') |
| description | text | | Room type description |
| base_price | decimal(10,2) | required | Base nightly rate |
| max_occupancy | integer | required | Maximum guests allowed |
| standard_occupancy | integer | required | Standard occupancy |
| extra_person_charge | decimal(10,2) | default: 0 | Charge for extra person |
| size_sqm | decimal | | Room size in square meters |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- has_many :rooms
- has_many :room_type_amenities
- has_many :amenities, through: :room_type_amenities

**Indexes:**
- index on [account_id, hotel_id]
- unique index on [hotel_id, code]

---

### Room ✅
Individual room instances in a hotel.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| room_type_id | bigint | FK, required | Room category |
| room_number | string | required | Room number/name |
| floor | string | | Floor/level |
| status | string | default: 'available' | available/occupied/maintenance/cleaning |
| is_smoking | boolean | default: false | Smoking allowed |
| notes | text | | Internal notes |
| last_cleaned_at | datetime | | Last cleaning timestamp |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- belongs_to :room_type
- has_many :reservations

**Indexes:**
- index on [account_id, hotel_id, status]
- unique index on [hotel_id, room_number]

---

### Guest ✅
Guest profiles with contact information and preferences.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| first_name | string | required | Guest first name |
| last_name | string | required | Guest last name |
| email | string | required | Email address |
| phone | string | | Phone number |
| date_of_birth | date | | Date of birth |
| nationality | string | | Country code (ISO) |
| passport_number | string | | Passport number |
| id_number | string | | National ID number |
| address | string | | Home address |
| city | string | | City |
| state | string | | State/Province |
| country | string | | Country code (ISO) |
| postal_code | string | | Postal/ZIP code |
| vip_status | boolean | default: false | VIP guest flag |
| notes | text | | Internal notes |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- has_many :reservations
- has_many :guest_preferences

**Indexes:**
- index on [account_id, email]
- index on [account_id, last_name]

---

### Reservation ✅
Hotel room reservations and bookings.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Hotel |
| guest_id | bigint | FK, required | Primary guest |
| room_id | bigint | FK | Assigned room (nullable until assignment) |
| room_type_id | bigint | FK, required | Requested room type |
| confirmation_number | string | required, unique | Booking confirmation code |
| check_in_date | date | required | Check-in date |
| check_out_date | date | required | Check-out date |
| actual_check_in | datetime | | Actual check-in timestamp |
| actual_check_out | datetime | | Actual check-out timestamp |
| status | string | default: 'pending' | pending/confirmed/checked_in/checked_out/cancelled/no_show |
| adults | integer | default: 1 | Number of adults |
| children | integer | default: 0 | Number of children |
| infants | integer | default: 0 | Number of infants |
| total_amount | decimal(10,2) | required | Total reservation amount |
| paid_amount | decimal(10,2) | default: 0 | Amount paid so far |
| currency | string | default: 'USD' | Currency code (ISO) |
| payment_status | string | default: 'pending' | pending/partial/paid/refunded |
| special_requests | text | | Guest special requests |
| internal_notes | text | | Internal staff notes |
| source | string | | Booking source (walk_in/phone/website/ota) |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- belongs_to :guest
- belongs_to :room (optional)
- belongs_to :room_type
- has_many :reservation_rooms
- has_many :payments
- has_many :charges

**Indexes:**
- index on [account_id, hotel_id, check_in_date]
- index on [account_id, hotel_id, status]
- unique index on confirmation_number

---

## Priority 2: Operational Essentials ⚠️

### RatePlan
Pricing plans for room types with different conditions and benefits.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| room_type_id | bigint | FK | Specific room type (null for all) |
| name | string | required | Rate plan name |
| code | string | required | Internal code |
| description | text | | Rate plan description |
| pricing_type | string | default: 'fixed' | fixed/dynamic/seasonal |
| base_price | decimal(10,2) | | Base rate (can be overridden) |
| min_stay | integer | default: 1 | Minimum nights |
| max_stay | integer | | Maximum nights |
| advance_booking_days | integer | | Required advance booking |
| cancellation_policy_id | bigint | FK | Cancellation policy |
| meal_plan | string | default: 'room_only' | room_only/breakfast/half_board/full_board |
| is_active | boolean | default: true | Active status |
| valid_from | date | | Validity start date |
| valid_to | date | | Validity end date |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- belongs_to :room_type (optional)
- belongs_to :cancellation_policy
- has_many :seasonal_rates

**Indexes:**
- index on [account_id, hotel_id, is_active]
- unique index on [hotel_id, code]

---

### SeasonalRate
Date-specific pricing for rate plans.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| rate_plan_id | bigint | FK, required | Parent rate plan |
| name | string | | Season name |
| start_date | date | required | Season start date |
| end_date | date | required | Season end date |
| base_price | decimal(10,2) | required | Base rate for period |
| weekend_price | decimal(10,2) | | Weekend rate (Fri-Sat) |
| min_nights | integer | | Minimum stay for period |
| max_nights | integer | | Maximum stay for period |
| closed_to_arrival | boolean | default: false | Block arrivals |
| closed_to_departure | boolean | default: false | Block departures |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :rate_plan

**Indexes:**
- index on [account_id, rate_plan_id, start_date]

---

### RoomInventory
Daily availability tracking for room types.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| room_type_id | bigint | FK, required | Room type |
| date | date | required | Inventory date |
| total_rooms | integer | required | Total rooms of this type |
| available_rooms | integer | required | Currently available |
| occupied_rooms | integer | default: 0 | Currently occupied |
| blocked_rooms | integer | default: 0 | Blocked/maintenance |
| min_stay | integer | | Minimum stay override |
| max_stay | integer | | Maximum stay override |
| stop_sell | boolean | default: false | Stop selling this date |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- belongs_to :room_type

**Indexes:**
- unique index on [hotel_id, room_type_id, date]
- index on [account_id, hotel_id, date]

---

### ReservationRoom
Support for multi-room reservations (one reservation, multiple rooms).

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| reservation_id | bigint | FK, required | Parent reservation |
| room_id | bigint | FK | Assigned room |
| room_type_id | bigint | FK, required | Room type |
| rate_plan_id | bigint | FK | Applied rate plan |
| adults | integer | default: 1 | Adults in this room |
| children | integer | default: 0 | Children in this room |
| nightly_rate | decimal(10,2) | required | Rate per night |
| total_amount | decimal(10,2) | required | Total for this room |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :reservation
- belongs_to :room (optional)
- belongs_to :room_type
- belongs_to :rate_plan (optional)

**Indexes:**
- index on [account_id, reservation_id]

---

### GuestPreference
Stored preferences for returning guests.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| guest_id | bigint | FK, required | Parent guest |
| preference_type | string | required | room/dietary/accessibility/other |
| preference_key | string | required | Preference identifier |
| preference_value | string | required | Preference value |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :guest

**Indexes:**
- index on [account_id, guest_id, preference_type]

---

### HotelPolicy ✅
Hotel-specific policies and rules.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| policy_type | string | required | check_in/check_out/cancellation/pet/smoking/children |
| title | string | required | Policy title |
| description | text | required | Policy details |
| sort_order | integer | default: 0 | Display order |
| is_active | boolean | default: true | Active status |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel

**Indexes:**
- index on [account_id, hotel_id, policy_type, is_active]

---

### Invoice
Financial invoices for reservations.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| reservation_id | bigint | FK, required | Parent reservation |
| invoice_number | string | required, unique | Invoice number |
| issue_date | date | required | Issue date |
| due_date | date | | Payment due date |
| status | string | default: 'draft' | draft/sent/paid/overdue/cancelled |
| subtotal | decimal(10,2) | required | Subtotal amount |
| tax_amount | decimal(10,2) | default: 0 | Tax amount |
| total_amount | decimal(10,2) | required | Total amount |
| paid_amount | decimal(10,2) | default: 0 | Amount paid |
| currency | string | default: 'USD' | Currency code (ISO) |
| notes | text | | Invoice notes |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :reservation
- has_many :payments

**Indexes:**
- index on [account_id, status]
- unique index on invoice_number

---

### Payment
Payment transactions for reservations and invoices.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| reservation_id | bigint | FK, required | Parent reservation |
| invoice_id | bigint | FK | Related invoice |
| amount | decimal(10,2) | required | Payment amount |
| currency | string | default: 'USD' | Currency code (ISO) |
| payment_method | string | required | cash/card/bank_transfer/online |
| payment_type | string | required | deposit/partial/full/refund |
| transaction_id | string | | External transaction ID |
| status | string | default: 'pending' | pending/processing/completed/failed/refunded |
| processed_at | datetime | | Processing timestamp |
| gateway_response | json | | Payment gateway response |
| notes | text | | Payment notes |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :reservation
- belongs_to :invoice (optional)

**Indexes:**
- index on [account_id, reservation_id]
- index on [account_id, status]

---

### Charge
Additional charges and fees for reservations.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| reservation_id | bigint | FK, required | Parent reservation |
| charge_type | string | required | room/service/damage/fee/tax/other |
| description | string | required | Charge description |
| amount | decimal(10,2) | required | Charge amount |
| quantity | integer | default: 1 | Quantity |
| unit_price | decimal(10,2) | | Price per unit |
| date | date | required | Charge date |
| taxable | boolean | default: true | Is taxable |
| tax_rate | decimal(5,2) | | Tax rate percentage |
| tax_amount | decimal(10,2) | | Tax amount |
| posted_by | bigint | FK | User who posted charge |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :reservation
- belongs_to :posted_by, class_name: 'User' (optional)

**Indexes:**
- index on [account_id, reservation_id, date]

---

## Priority 3: Enhanced Operations ✅

### HousekeepingTask ✅
Task management for housekeeping operations.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| room_id | bigint | FK, required | Target room |
| assigned_to | bigint | FK | Assigned staff member |
| task_type | string | required | cleaning/inspection/turndown/deep_clean |
| priority | string | default: 'normal' | low/normal/high/urgent |
| status | string | default: 'pending' | pending/in_progress/completed/cancelled |
| scheduled_date | date | required | Scheduled date |
| scheduled_time | time | | Scheduled time |
| started_at | datetime | | Task start time |
| completed_at | datetime | | Task completion time |
| inspection_score | integer | | Quality score (1-10) |
| notes | text | | Task notes |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- belongs_to :room
- belongs_to :assigned_to, class_name: 'User' (optional)

**Indexes:**
- index on [account_id, hotel_id, scheduled_date, status]
- index on [account_id, room_id, status]

---

### MaintenanceRequest ✅
Maintenance and repair tracking.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| room_id | bigint | FK | Target room (null for common areas) |
| reported_by | bigint | FK, required | Reporting user |
| assigned_to | bigint | FK | Assigned staff member |
| category | string | required | electrical/plumbing/hvac/furniture/other |
| issue_description | text | required | Issue details |
| priority | string | default: 'normal' | low/normal/high/critical |
| status | string | default: 'open' | open/in_progress/pending_parts/completed/cancelled |
| cost_estimate | decimal(10,2) | | Estimated repair cost |
| actual_cost | decimal(10,2) | | Actual repair cost |
| scheduled_date | date | | Scheduled repair date |
| completed_at | datetime | | Completion timestamp |
| resolution_notes | text | | Resolution details |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- belongs_to :room (optional)
- belongs_to :reported_by, class_name: 'User'
- belongs_to :assigned_to, class_name: 'User' (optional)

**Indexes:**
- index on [account_id, hotel_id, status]
- index on [account_id, room_id, status]

---

### Amenity ✅
Master list of available amenities.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| name | string | required | Amenity name |
| category | string | required | room/hotel/bathroom/kitchen/entertainment |
| icon | string | | Icon identifier |
| description | text | | Amenity description |
| is_countable | boolean | default: false | Can have quantity |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- has_many :hotel_amenities
- has_many :hotels, through: :hotel_amenities
- has_many :room_type_amenities
- has_many :room_types, through: :room_type_amenities

**Indexes:**
- index on [account_id, category]

---

### HotelAmenity ✅
Hotel-level amenities (pool, gym, restaurant).

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| amenity_id | bigint | FK, required | Amenity reference |
| is_available | boolean | default: true | Currently available |
| is_free | boolean | default: true | Free for guests |
| additional_cost | decimal(10,2) | | Cost if not free |
| location | string | | Location in hotel |
| operating_hours | string | | Operating hours |
| notes | text | | Additional notes |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- belongs_to :amenity

**Indexes:**
- unique index on [hotel_id, amenity_id]
- index on [account_id, hotel_id, is_available]

---

### RoomTypeAmenity ✅
Amenities specific to room types.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| room_type_id | bigint | FK, required | Parent room type |
| amenity_id | bigint | FK, required | Amenity reference |
| quantity | integer | default: 1 | Quantity (e.g., 2 beds) |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :room_type
- belongs_to :amenity

**Indexes:**
- unique index on [room_type_id, amenity_id]

---

### Service ✅
Additional services offered by the hotel.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| name | string | required | Service name |
| category | string | required | spa/dining/transport/laundry/other |
| description | text | | Service description |
| price | decimal(10,2) | required | Service price |
| pricing_unit | string | default: 'fixed' | fixed/per_person/per_hour/per_item |
| duration_minutes | integer | | Service duration |
| capacity | integer | | Max simultaneous bookings |
| advance_booking_required | boolean | default: false | Requires advance booking |
| is_active | boolean | default: true | Service availability |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel
- has_many :service_bookings
- has_many :service_schedules

**Indexes:**
- index on [account_id, hotel_id, is_active]

---

### ServiceBooking ✅
Guest bookings for additional services.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| reservation_id | bigint | FK | Parent reservation |
| service_id | bigint | FK, required | Booked service |
| guest_id | bigint | FK, required | Guest booking service |
| booking_date | date | required | Service date |
| booking_time | time | | Service time |
| duration_minutes | integer | | Booking duration |
| quantity | integer | default: 1 | Quantity booked |
| unit_price | decimal(10,2) | required | Price per unit |
| total_price | decimal(10,2) | required | Total price |
| status | string | default: 'pending' | pending/confirmed/completed/cancelled |
| notes | text | | Booking notes |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :reservation (optional)
- belongs_to :service
- belongs_to :guest

**Indexes:**
- index on [account_id, booking_date, status]
- index on [account_id, service_id, booking_date]

---

### ServiceSchedule ✅
Operating schedules for services.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| service_id | bigint | FK, required | Parent service |
| day_of_week | integer | required | Day (0=Sunday, 6=Saturday) |
| start_time | time | required | Service start time |
| end_time | time | required | Service end time |
| max_bookings_per_slot | integer | | Capacity per time slot |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :service

**Indexes:**
- index on [account_id, service_id, day_of_week]

---

### CancellationPolicy ✅
Configurable cancellation policies.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK | Hotel-specific (null for account-wide) |
| name | string | required | Policy name |
| code | string | required | Policy code |
| description | text | | Policy description |
| cancellation_type | string | required | flexible/moderate/strict/non_refundable |
| free_cancellation_hours | integer | | Hours before check-in for free cancellation |
| penalty_percentage | decimal(5,2) | | Penalty percentage |
| penalty_nights | integer | | Penalty in nights charged |
| no_show_penalty | decimal(5,2) | | No-show penalty percentage |
| is_refundable | boolean | default: true | Allows refunds |
| is_default | boolean | default: false | Default policy |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel (optional)
- has_many :rate_plans

**Indexes:**
- index on [account_id, hotel_id, is_default]

---

### TaxConfiguration
Tax settings and rates.

| Field | Type | Constraints | Description |
|-------|------|------------|-------------|
| id | bigint | PK | Primary key |
| account_id | bigint | FK, required | Parent account (for scoping) |
| hotel_id | bigint | FK, required | Parent hotel |
| tax_name | string | required | Tax name (e.g., VAT, GST) |
| tax_code | string | required | Tax code |
| tax_rate | decimal(5,2) | required | Tax percentage |
| tax_type | string | default: 'percentage' | percentage/fixed |
| applies_to | string | default: 'all' | room/service/all |
| is_inclusive | boolean | default: false | Tax included in price |
| is_active | boolean | default: true | Active status |
| created_at | datetime | required | |
| updated_at | datetime | required | |

**Relationships:**
- belongs_to :account
- belongs_to :hotel

**Indexes:**
- index on [account_id, hotel_id, is_active]

---

## Database Considerations

### Indexes Strategy
1. All foreign keys should be indexed
2. Composite indexes on (account_id + frequently queried fields)
3. Date-based queries need indexes on date fields
4. Status fields that are frequently filtered need indexes

### Data Integrity
1. Check constraints on dates (check_out > check_in)
2. Positive number constraints on amounts and quantities
3. Enum validations on status fields
4. Referential integrity with proper CASCADE/RESTRICT rules

### Performance Optimization
1. Consider partitioning large tables by date (reservations, room_inventory)
2. Archive old reservations after X months
3. Use counter caches for frequently counted associations
4. Implement database-level constraints for business rules

### Security Considerations
1. Encrypt sensitive fields (passport_number, credit card info)
2. Audit trail for financial transactions
3. Soft deletes for critical records
4. Row-level security via acts_as_tenant

### Future Considerations
- **Versioning**: Add paper_trail to Reservation, Payment, Invoice
- **Soft Deletes**: Add paranoia gem for critical models
- **Audit Logs**: Track all changes to financial records
- **File Attachments**: Use Active Storage for documents and images