# Controllers & Authorization Guide

## Overview
This document defines the controllers, actions, and Pundit policies for the hotel management system MVP. Authorization is based on five existing roles: `owner`, `admin`, `hotel_manager`, `front_desk`, and `housekeeping`.

## Existing Roles

- **owner** - Full system access, account management
- **admin** - Administrative access, user management
- **hotel_manager** - Full hotel operations control
- **front_desk** - Reservations, check-ins/outs, guest management
- **housekeeping** - Room status updates only

## Staff-Facing Controllers

### DashboardController
**Purpose:** Main dashboard after login

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `show` | Display dashboard | owner, admin, hotel_manager, front_desk, housekeeping |

### HotelsController
**Purpose:** Manage hotels within an account

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `index` | List hotels in account | owner, admin, hotel_manager, front_desk, housekeeping |
| `show` | View hotel details | owner, admin, hotel_manager, front_desk, housekeeping |
| `new` | New hotel form | owner, admin |
| `create` | Create hotel | owner, admin |
| `edit` | Edit hotel form | owner, admin, hotel_manager |
| `update` | Update hotel | owner, admin, hotel_manager |
| `destroy` | Delete hotel | owner, admin |
| `set_current` | Switch active hotel | owner, admin, hotel_manager, front_desk, housekeeping |

### RoomTypesController
**Purpose:** Define room categories (Standard, Deluxe, Suite, etc.)

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `index` | List room types | owner, admin, hotel_manager, front_desk |
| `show` | View room type details | owner, admin, hotel_manager, front_desk |
| `new` | New room type form | owner, admin, hotel_manager |
| `create` | Create room type | owner, admin, hotel_manager |
| `edit` | Edit room type form | owner, admin, hotel_manager |
| `update` | Update room type | owner, admin, hotel_manager |
| `destroy` | Delete room type | owner, admin, hotel_manager |

### RoomsController
**Purpose:** Manage individual room instances

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `index` | List rooms | owner, admin, hotel_manager, front_desk, housekeeping |
| `show` | View room details | owner, admin, hotel_manager, front_desk, housekeeping |
| `new` | New room form | owner, admin, hotel_manager |
| `create` | Create room | owner, admin, hotel_manager |
| `edit` | Edit room form | owner, admin, hotel_manager, housekeeping* |
| `update` | Update room | owner, admin, hotel_manager, housekeeping* |
| `destroy` | Delete room | owner, admin, hotel_manager |

*Housekeeping can only update room status fields

### GuestsController
**Purpose:** Guest profiles and history

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `index` | Search guests | owner, admin, hotel_manager, front_desk |
| `show` | Guest profile | owner, admin, hotel_manager, front_desk |
| `new` | New guest form | owner, admin, hotel_manager, front_desk |
| `create` | Create guest | owner, admin, hotel_manager, front_desk |
| `edit` | Edit guest form | owner, admin, hotel_manager, front_desk |
| `update` | Update guest | owner, admin, hotel_manager, front_desk |
| `destroy` | Delete guest | owner, admin, hotel_manager |

### ReservationsController
**Purpose:** Core booking operations

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `index` | List reservations | owner, admin, hotel_manager, front_desk |
| `show` | Reservation details | owner, admin, hotel_manager, front_desk |
| `new` | New reservation form | owner, admin, hotel_manager, front_desk |
| `create` | Create reservation | owner, admin, hotel_manager, front_desk |
| `edit` | Edit reservation | owner, admin, hotel_manager, front_desk |
| `update` | Update reservation | owner, admin, hotel_manager, front_desk |
| `destroy` | Cancel reservation | owner, admin, hotel_manager, front_desk |

### CheckInsController
**Purpose:** Handle check-in process

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `new` | Check-in form | owner, admin, hotel_manager, front_desk |
| `create` | Process check-in | owner, admin, hotel_manager, front_desk |

### CheckOutsController
**Purpose:** Handle check-out process

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `new` | Check-out form | owner, admin, hotel_manager, front_desk |
| `create` | Process check-out | owner, admin, hotel_manager, front_desk |

### AccountUsersController
**Purpose:** Manage users within an account

| Action | Description | Authorized Roles |
|--------|-------------|------------------|
| `index` | List users in account | owner, admin |
| `new` | Invite user form | owner, admin |
| `create` | Send invitation | owner, admin |
| `destroy` | Remove user from account | owner, admin |

## Consumer-Facing Controllers

### Public::HotelsController
**Purpose:** Public hotel information and main entry point
**Authentication:** None required

| Action | Description |
|--------|-------------|
| `show` | Public hotel landing page with amenities, location, and room previews |

### Public::RoomsController
**Purpose:** Room browsing and detailed room information
**Authentication:** None required

| Action | Description |
|--------|-------------|
| `index` | Browse all room types with availability for selected dates |
| `show` | Detailed room view with photos, amenities, and booking options |

### Public::AvailabilityController
**Purpose:** Room availability search
**Authentication:** None required

| Action | Description |
|--------|-------------|
| `index` | Search available rooms by dates/guests |

### Public::BookingsController
**Purpose:** Guest booking flow
**Authentication:** None required

| Action | Description |
|--------|-------------|
| `new` | Booking form with selected room/dates |
| `create` | Create booking (creates Guest + Reservation) |
| `show` | Booking confirmation page |

### Public::ReservationsController
**Purpose:** Guest reservation management
**Authentication:** None required (uses confirmation number + email/phone)

| Action | Description |
|--------|-------------|
| `show` | View reservation details |

## Pundit Policy Implementation

### Base Policy Rules

```ruby
class ApplicationPolicy
  def initialize(user, record)
    @user = user
    @record = record
  end

  private

  def owner?
    @user.has_role?(:owner)
  end

  def admin?
    @user.has_role?(:admin)
  end

  def hotel_manager?
    @user.has_role?(:hotel_manager)
  end

  def front_desk?
    @user.has_role?(:front_desk)
  end

  def housekeeping?
    @user.has_role?(:housekeeping)
  end
end
```

### Authorization Hierarchy

1. **Owner/Admin**: Full access to all controllers and actions
2. **Hotel Manager**: Full hotel operations, no account-level user management
3. **Front Desk**: Guest services, reservations, check-ins/outs
4. **Housekeeping**: View rooms, update room status only

### Multi-Tenancy Considerations

- All staff controllers scope data by `current_account`
- Public controllers may scope by hotel subdomain if implemented
- Tenant isolation enforced via `acts_as_tenant`

## URL Structure

### Staff URLs
```
app.domain.com/                    # Dashboard
app.domain.com/hotels              # Hotels list
app.domain.com/reservations        # Reservations
app.domain.com/guests              # Guests
app.domain.com/rooms               # Rooms
```

### Public URLs (Consumer Journey)
```
domain.com/public/hotel            # Hotel landing page (entry point)
domain.com/public/rooms            # Browse all rooms with availability
domain.com/public/rooms/:id        # Detailed room view
domain.com/public/availability     # Room availability search
domain.com/public/bookings/new     # Make booking
domain.com/public/bookings/:id     # Booking confirmation
domain.com/public/reservations/lookup  # Find existing reservation
domain.com/public/reservations/:id     # View reservation details
```

### Consumer Flow
1. **Discovery**: Land on hotel page → View amenities and location
2. **Browse**: Explore all room types → See availability and pricing
3. **Details**: View specific room → Photos, amenities, descriptions
4. **Book**: Select room → Enter guest details → Receive confirmation

## Key Implementation Notes

1. **Authentication**: Staff controllers require Devise authentication
2. **Authorization**: All staff actions check Pundit policies
3. **Tenant Scoping**: Controllers use `set_current_tenant_through_filter`
4. **Public Access**: Consumer controllers bypass authentication
5. **Confirmation Security**: Public reservation viewing requires confirmation number + contact info
6. **Hotel Management**: 
   - Only owners and admins can create/delete hotels
   - Hotels with existing reservations cannot be deleted
   - Deleting current hotel automatically switches to another hotel
   - Creating first hotel automatically sets it as current
7. **Current Hotel Context**: 
   - Maintained in session via `set_current_hotel` action
   - Hotel switcher available in sidebar for all staff
   - Room and reservation operations scoped to current hotel