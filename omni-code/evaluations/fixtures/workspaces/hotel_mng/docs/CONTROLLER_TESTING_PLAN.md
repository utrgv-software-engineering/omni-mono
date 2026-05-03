# Controller Testing Plan

Based on the 37signals Testing Guide (docs/TESTING_GUIDE.md), this plan prioritizes **controller tests as the primary testing layer** (Section 3.0).

## Testing Philosophy

Per the guide:
- **Controller tests are primary** - verify behavior through the same interface users will use
- **Use real objects and database interactions** - avoid mocks and stubs
- **Test behavior that matters** - skip testing Rails defaults
- **Test both successful and failed scenarios**
- **Verify database state changes**

## Scope

This plan covers **non-API controllers only**:
- 16 Staff Controllers (require authentication + authorization)
- 5 Public Controllers (no authentication, email/phone verification)
- 2 Devise Controllers (registration/session customizations)

---

## Phase 1: Core Staff Controllers (High Priority)

These controllers handle the primary hotel operations workflow.

### 1.1 ReservationsController

**Routes:** `resources :reservations`

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | front_desk+ | List with filters (status, dates, search), pagination |
| show | front_desk+ | Display reservation details |
| new/create | front_desk+ | Generate confirmation number, validate dates/occupancy |
| edit/update | front_desk+ | Modify reservation, prevent changes if checked_in |
| destroy | manager+ | Cancel reservation (not hard delete) |

**Test Cases:**
```
Authorization:
- [ ] front_desk can access index, show, create, update
- [ ] front_desk cannot destroy
- [ ] hotel_manager can destroy
- [ ] housekeeping cannot access any action

CRUD Operations:
- [ ] index returns reservations for current hotel only
- [ ] index filters by status (pending, confirmed, checked_in, etc.)
- [ ] index filters by date range
- [ ] index searches by guest name, email, confirmation number
- [ ] create generates unique confirmation number (format: 8 alphanumeric)
- [ ] create validates check_out_date > check_in_date
- [ ] create validates total_guests <= room_type.max_occupancy
- [ ] create sets status to 'pending' by default
- [ ] update prevents modification of checked_in reservations (if applicable)
- [ ] destroy changes status to 'cancelled' (soft delete)

Edge Cases:
- [ ] duplicate confirmation number handling
- [ ] overlapping reservation dates for same room
```

### 1.2 CheckInsController

**Routes:** `resource :check_in` (nested under reservations)

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| new | front_desk+ | Show available rooms for room_type |
| create | front_desk+ | Assign room, update statuses, set timestamp |

**Test Cases:**
```
Authorization:
- [ ] front_desk can check in guests
- [ ] housekeeping cannot check in guests

Business Logic:
- [ ] new shows only available rooms matching room_type
- [ ] create assigns room to reservation
- [ ] create updates reservation.status to 'checked_in'
- [ ] create updates room.status to 'occupied'
- [ ] create sets reservation.actual_check_in timestamp
- [ ] create fails if no room_id provided
- [ ] create fails if room is not available
- [ ] create fails if reservation is not 'confirmed'

State Transitions:
- [ ] reservation: confirmed → checked_in
- [ ] room: available → occupied
```

### 1.3 CheckOutsController

**Routes:** `resource :check_out` (nested under reservations)

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| new | front_desk+ | Show balance due, pending charges |
| create | front_desk+ | Process payment, update statuses |

**Test Cases:**
```
Authorization:
- [ ] front_desk can check out guests
- [ ] housekeeping cannot check out guests

Business Logic:
- [ ] new displays balance_due (total_amount - paid_amount)
- [ ] create updates reservation.status to 'checked_out'
- [ ] create updates reservation.actual_check_out timestamp
- [ ] create updates room.status to 'cleaning'
- [ ] create updates payment_status based on amount paid
- [ ] create handles partial payments
- [ ] create fails if reservation is not 'checked_in'

State Transitions:
- [ ] reservation: checked_in → checked_out
- [ ] room: occupied → cleaning
- [ ] payment_status: pending → partial/paid
```

### 1.4 GuestsController

**Routes:** `resources :guests`

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | front_desk+ | List with search, pagination |
| show | front_desk+ | Display guest with reservation history |
| new/create | front_desk+ | Create guest profile |
| edit/update | front_desk+ | Update guest details |
| destroy | manager+ | Delete guest |

**Test Cases:**
```
Authorization:
- [ ] front_desk can CRUD (except destroy)
- [ ] manager can destroy
- [ ] housekeeping cannot access

CRUD Operations:
- [ ] index searches by first_name, last_name, email (case-insensitive)
- [ ] show includes reservation history
- [ ] create validates required fields (first_name, last_name, email)
- [ ] destroy fails if guest has active reservations (if implemented)
```

### 1.5 RoomsController

**Routes:** `resources :rooms` (nested under hotels)

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | any_staff | List rooms by hotel |
| show | any_staff | Display room details |
| new/create | manager+ | Create room |
| edit/update | manager+ OR housekeeping* | Update room (*restricted params) |
| destroy | manager+ | Delete room |

**Test Cases:**
```
Authorization:
- [ ] any staff can view rooms (index, show)
- [ ] manager can create, update all fields, destroy
- [ ] housekeeping can ONLY update status and last_cleaned_at
- [ ] housekeeping cannot update room_number, floor, room_type_id, etc.
- [ ] front_desk cannot create or destroy

Housekeeping Restriction (Critical):
- [ ] housekeeping update with status: 'available' succeeds
- [ ] housekeeping update with room_number: '999' is ignored/rejected
- [ ] housekeeping update sets last_cleaned_at correctly

CRUD Operations:
- [ ] index returns rooms for specified hotel only
- [ ] create associates room with hotel
- [ ] update room status (available, occupied, maintenance, cleaning)
- [ ] destroy fails if room has active reservations
```

### 1.6 RoomTypesController

**Routes:** `resources :room_types` (nested under hotels)

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | front_desk+ | List room types by hotel |
| show | front_desk+ | Display room type details |
| new/create | manager+ | Create room type |
| edit/update | manager+ | Update room type |
| destroy | manager+ | Delete room type |

**Test Cases:**
```
Authorization:
- [ ] front_desk can view (index, show)
- [ ] front_desk cannot create, update, destroy
- [ ] manager can full CRUD

CRUD Operations:
- [ ] create validates base_price, max_occupancy, standard_occupancy
- [ ] create validates standard_occupancy <= max_occupancy
- [ ] destroy fails if room_type has rooms assigned
```

---

## Phase 2: Operational Staff Controllers

### 2.1 HousekeepingTasksController

**Routes:** `resources :housekeeping_tasks` (nested under hotels)

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | any_staff | List with filters |
| show | any_staff | Display task details |
| new/create | front_desk+ | Create task |
| edit/update | front_desk+ OR housekeeping* | Update task (*restricted) |
| destroy | manager+ | Delete task |
| mark_complete | front_desk+ | Complete with optional score |
| assign | front_desk+ | Assign to user |

**Test Cases:**
```
Authorization:
- [ ] any staff can view tasks
- [ ] housekeeping can only update status, completed_at, notes
- [ ] housekeeping cannot update priority, task_type, scheduled_date
- [ ] front_desk can update all fields
- [ ] front_desk can mark_complete and assign
- [ ] housekeeping cannot mark_complete or assign

Custom Actions:
- [ ] mark_complete sets status to 'completed'
- [ ] mark_complete sets completed_at timestamp
- [ ] mark_complete accepts optional inspection_score (1-10)
- [ ] assign updates assigned_to_id

Filtering:
- [ ] index filters by status
- [ ] index filters by scheduled_date
- [ ] index filters by room_id
```

### 2.2 MaintenanceRequestsController

**Routes:** `resources :maintenance_requests` (nested under hotels)

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | any_staff | List with filters |
| show | any_staff | Display request details |
| new/create | any_staff | Create request (anyone can report) |
| edit/update | front_desk+ | Update request |
| destroy | manager+ | Delete request |
| assign | front_desk+ | Assign to user |
| complete | front_desk+ | Complete with resolution notes |
| cancel | front_desk+ | Cancel with reason |

**Test Cases:**
```
Authorization:
- [ ] any staff (including housekeeping) can create requests
- [ ] housekeeping cannot update, assign, complete, cancel
- [ ] front_desk can update, assign, complete, cancel
- [ ] manager can destroy

Business Logic:
- [ ] create auto-sets reported_by to current user
- [ ] complete sets status, completed_at, resolution_notes, actual_cost
- [ ] cancel sets status and resolution_notes (reason)

Filtering:
- [ ] index filters by status
- [ ] index filters by priority
- [ ] index filters by category
- [ ] index filters by room_id
```

---

## Phase 3: Account & Hotel Management

### 3.1 AccountUsersController

**Routes:** `resources :account_users`

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | owner/admin | List users grouped by role |
| new/create | owner/admin | Invite user or create AI agent |
| edit/update | owner/admin | Update role |
| destroy | owner/admin | Remove from account |

**Test Cases:**
```
Authorization:
- [ ] only owner and admin can access
- [ ] hotel_manager cannot access
- [ ] front_desk cannot access

User Invitation:
- [ ] create with existing user email adds to account
- [ ] create with new user email sends invitation
- [ ] create AI agent (email: name@subdomain.ai) creates directly
- [ ] AI agent gets api_key generated

Role Management:
- [ ] update changes user role
- [ ] cannot remove own account_user
- [ ] cannot remove last owner from account

Business Rules:
- [ ] last owner protection on destroy
- [ ] transaction rollback on failure
```

### 3.2 HotelsController

**Routes:** `resources :hotels`

**Actions to Test:**

| Action | Roles Allowed | Key Behaviors |
|--------|---------------|---------------|
| index | front_desk+ | List hotels in account |
| show | front_desk+ | Display hotel details |
| new/create | owner/admin | Create hotel |
| edit/update | owner/admin | Update hotel |
| destroy | owner/admin | Delete hotel |
| set_current | front_desk+ | Switch hotel context |

**Test Cases:**
```
Authorization:
- [ ] front_desk can view hotels
- [ ] front_desk cannot create, update, destroy
- [ ] owner/admin can full CRUD

Business Logic:
- [ ] create auto-sets as current_hotel if first hotel
- [ ] destroy fails if hotel has reservations
- [ ] set_current updates session[:current_hotel_id]

Multi-Hotel:
- [ ] index shows all hotels user can access
- [ ] policy scoping limits hotels by role
```

---

## Phase 4: Configuration Controllers

### 4.1 AmenitiesController (Account-wide)

**Routes:** `resources :amenities`

| Action | Roles Allowed |
|--------|---------------|
| index/show | front_desk+ |
| create/update/destroy | manager+ |

### 4.2 HotelAmenitiesController

**Routes:** `resources :hotel_amenities` (nested under hotels)

| Action | Roles Allowed |
|--------|---------------|
| index/show | front_desk+ |
| create/update/destroy | manager+ |

### 4.3 CancellationPoliciesController

**Routes:** `resources :cancellation_policies`

| Action | Roles Allowed |
|--------|---------------|
| index/show | front_desk+ |
| create/update/destroy | manager+ |

### 4.4 ServicesController & ServiceBookingsController

**Routes:** `resources :services` (nested under hotels), `resources :service_bookings`

| Action | Roles Allowed |
|--------|---------------|
| index/show | front_desk+ |
| create/update | front_desk+ (bookings), manager+ (services) |
| destroy | manager+ |

---

## Phase 5: Public Controllers (No Authentication)

### 5.1 Public::BookingsController

**Routes:** `resources :bookings, only: [:new, :create, :show]`

**Test Cases:**
```
Guest Booking Flow:
- [ ] new displays booking form with room_type, dates, guests
- [ ] create finds or creates guest by email
- [ ] create generates unique confirmation number
- [ ] create calculates total_amount from room_type pricing
- [ ] create sets status to 'confirmed', source to 'website'
- [ ] create sends confirmation email
- [ ] show displays booking confirmation

Tenant Context:
- [ ] booking creates records in correct account
- [ ] hotel resolved from subdomain or param
```

### 5.2 Public::ReservationsController

**Routes:** Custom routes for lookup, search, modify, cancel

**Test Cases:**
```
Lookup & Verification:
- [ ] lookup displays search form
- [ ] search requires confirmation_number
- [ ] search requires email OR phone for verification
- [ ] search is case-insensitive for email
- [ ] search returns 404 if not found or verification fails

Modification:
- [ ] edit displays form if can_modify? is true
- [ ] edit redirects if cannot modify (checked_in, past date, etc.)
- [ ] update checks availability for new dates
- [ ] update recalculates price
- [ ] update clears room assignment if dates/room_type changed
- [ ] update logs changes in internal_notes
- [ ] update sends modification email

Cancellation:
- [ ] cancel displays cancellation page if can_cancel?
- [ ] cancel shows policy details and estimated refund
- [ ] confirm_cancel updates status to 'cancelled'
- [ ] confirm_cancel sends cancellation email with refund info
- [ ] cancelled displays confirmation page

Business Rules (can_modify?, can_cancel?):
- [ ] cannot modify if checked_in
- [ ] cannot modify if < 24 hours before check_in
- [ ] cannot cancel if checked_in
- [ ] cannot cancel if past check_in_date
```

### 5.3 Public::AvailabilityController

**Routes:** `resources :availability, only: [:index]`

**Test Cases:**
```
- [ ] index accepts check_in_date, check_out_date, guests params
- [ ] index defaults to today/tomorrow if no dates
- [ ] index filters room_types by max_occupancy >= guests
- [ ] index calculates available_count for each room_type
- [ ] index excludes room_types with 0 availability
```

---

## Phase 6: Devise Controllers

### 6.1 Users::RegistrationsController

**Test Cases:**
```
Account Creation:
- [ ] create user creates Account with subdomain
- [ ] subdomain generated from account_name (lowercase, alphanumeric)
- [ ] subdomain is unique (appends number if collision)
- [ ] AccountUser created with owner role
- [ ] trial_ends_at set to 30 days from now
- [ ] transaction rolls back if any step fails

Session Setup:
- [ ] current_account set after registration
- [ ] redirects to dashboard
```

### 6.2 Users::SessionsController

**Test Cases:**
```
Login:
- [ ] sets current_account after login
- [ ] handles users with multiple accounts (selects first)

Logout:
- [ ] clears account from session
```

---

## Test Infrastructure Requirements

### Test Helpers Needed

```ruby
# test/support/controller_test_helper.rb

module ControllerTestHelper
  # Sign in and set up account context
  def sign_in_as(role, account: nil, hotel: nil)
    account ||= create(:account)
    hotel ||= create(:hotel, account: account)
    user = create(:user)
    account_user = AccountUser.create!(account: account, user: user)
    account_user.add_role(role)

    sign_in user
    ActsAsTenant.current_tenant = account
    session[:current_hotel_id] = hotel.id

    { user: user, account: account, hotel: hotel, account_user: account_user }
  end

  # For public controllers - set up hotel context without auth
  def set_public_hotel_context(hotel)
    # Simulate subdomain resolution or param
    @hotel = hotel
    ActsAsTenant.current_tenant = hotel.account
  end
end
```

### Factory Traits Needed

```ruby
# Ensure factories support all roles
factory :account_user do
  trait :owner do
    after(:create) { |au| au.add_role(:owner) }
  end
  trait :admin do
    after(:create) { |au| au.add_role(:admin) }
  end
  trait :hotel_manager do
    after(:create) { |au| au.add_role(:hotel_manager) }
  end
  trait :front_desk do
    after(:create) { |au| au.add_role(:front_desk) }
  end
  trait :housekeeping do
    after(:create) { |au| au.add_role(:housekeeping) }
  end
end
```

---

## Test File Structure

```
test/
├── controllers/
│   ├── reservations_controller_test.rb
│   ├── check_ins_controller_test.rb
│   ├── check_outs_controller_test.rb
│   ├── guests_controller_test.rb
│   ├── rooms_controller_test.rb
│   ├── room_types_controller_test.rb
│   ├── housekeeping_tasks_controller_test.rb
│   ├── maintenance_requests_controller_test.rb
│   ├── account_users_controller_test.rb
│   ├── hotels_controller_test.rb
│   ├── amenities_controller_test.rb
│   ├── hotel_amenities_controller_test.rb
│   ├── cancellation_policies_controller_test.rb
│   ├── services_controller_test.rb
│   ├── service_bookings_controller_test.rb
│   └── dashboard_controller_test.rb
├── controllers/public/
│   ├── bookings_controller_test.rb
│   ├── reservations_controller_test.rb
│   ├── availability_controller_test.rb
│   ├── hotels_controller_test.rb
│   └── rooms_controller_test.rb
├── controllers/users/
│   ├── registrations_controller_test.rb
│   └── sessions_controller_test.rb
└── support/
    └── controller_test_helper.rb
```

---

## Priority Order for Implementation

### Tier 1 (Critical Path - Implement First)
1. ReservationsController
2. CheckInsController
3. CheckOutsController
4. RoomsController (including housekeeping restriction)
5. Public::BookingsController
6. Public::ReservationsController

### Tier 2 (Core Operations)
7. GuestsController
8. RoomTypesController
9. HousekeepingTasksController
10. MaintenanceRequestsController

### Tier 3 (Account Management)
11. AccountUsersController
12. HotelsController
13. Users::RegistrationsController

### Tier 4 (Configuration)
14. AmenitiesController
15. HotelAmenitiesController
16. CancellationPoliciesController
17. ServicesController
18. ServiceBookingsController

### Tier 5 (Support)
19. DashboardController
20. Public::AvailabilityController
21. Public::HotelsController
22. Public::RoomsController

---

## Key Testing Patterns

### Authorization Test Pattern
```ruby
test "front_desk cannot destroy reservation" do
  sign_in_as(:front_desk)
  reservation = create(:reservation, hotel: @hotel)

  assert_no_difference 'Reservation.count' do
    delete reservation_path(reservation)
  end

  assert_response :forbidden # or redirect with flash
end
```

### State Transition Test Pattern
```ruby
test "check_in updates reservation and room status" do
  sign_in_as(:front_desk)
  reservation = create(:reservation, :confirmed, hotel: @hotel)
  room = create(:room, :available, hotel: @hotel, room_type: reservation.room_type)

  post reservation_check_in_path(reservation), params: { room_id: room.id }

  reservation.reload
  room.reload

  assert_equal 'checked_in', reservation.status
  assert_equal 'occupied', room.status
  assert_not_nil reservation.actual_check_in
end
```

### Public Controller Test Pattern
```ruby
test "search verifies guest email before showing reservation" do
  reservation = create(:reservation, guest: create(:guest, email: 'guest@example.com'))

  # Wrong email - should not find
  post public_reservation_search_path, params: {
    confirmation_number: reservation.confirmation_number,
    email: 'wrong@example.com'
  }
  assert_response :not_found

  # Correct email - should find
  post public_reservation_search_path, params: {
    confirmation_number: reservation.confirmation_number,
    email: 'guest@example.com'
  }
  assert_response :success
end
```

---

## Notes on Expected Behavior vs Bugs

When tests fail, consider:

1. **Implementation Bug**: Code doesn't match documented/expected behavior
2. **Test Bug**: Test assumptions are wrong
3. **Missing Feature**: Behavior not yet implemented
4. **Edge Case**: Undefined behavior that needs product decision

Document any ambiguities found during testing in this file for discussion.
