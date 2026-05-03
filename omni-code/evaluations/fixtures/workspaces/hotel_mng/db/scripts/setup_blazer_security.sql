-- PostgreSQL script to set up read-only user and Row Level Security for Blazer
-- Run this script as a superuser in your PostgreSQL database

-- 1. Create a read-only role for Blazer
-- Note: Change the password in production!
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'blazer_readonly') THEN
        CREATE ROLE blazer_readonly LOGIN PASSWORD 'blazer_secure_password_change_me';
    END IF;
END
$$;

-- 2. Grant connection and schema permissions
GRANT CONNECT ON DATABASE hotel_management_development TO blazer_readonly;
GRANT USAGE ON SCHEMA public TO blazer_readonly;

-- 3. Grant SELECT on all existing tables
GRANT SELECT ON ALL TABLES IN SCHEMA public TO blazer_readonly;

-- 4. Grant SELECT on all future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO blazer_readonly;

-- 5. Enable Row Level Security on tenant-scoped tables
-- This ensures data isolation between accounts

-- Enable RLS on all main tables
ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE hotels ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_types ENABLE ROW LEVEL SECURITY;
ALTER TABLE rooms ENABLE ROW LEVEL SECURITY;
ALTER TABLE guests ENABLE ROW LEVEL SECURITY;
ALTER TABLE reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE amenities ENABLE ROW LEVEL SECURITY;
ALTER TABLE hotel_amenities ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_type_amenities ENABLE ROW LEVEL SECURITY;
ALTER TABLE services ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE service_bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE housekeeping_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE maintenance_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE cancellation_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE hotel_policies ENABLE ROW LEVEL SECURITY;

-- 6. Create policies for each table to filter by account_id
-- These policies use a session variable that will be set by the application

-- Accounts table (special case - users can only see their own account)
CREATE POLICY blazer_account_policy ON accounts
    FOR SELECT TO blazer_readonly
    USING (id = current_setting('blazer.account_id', true)::bigint);

-- Hotels table
CREATE POLICY blazer_hotels_policy ON hotels
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Room types table
CREATE POLICY blazer_room_types_policy ON room_types
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Rooms table
CREATE POLICY blazer_rooms_policy ON rooms
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Guests table
CREATE POLICY blazer_guests_policy ON guests
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Reservations table
CREATE POLICY blazer_reservations_policy ON reservations
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Amenities table
CREATE POLICY blazer_amenities_policy ON amenities
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Hotel amenities table
CREATE POLICY blazer_hotel_amenities_policy ON hotel_amenities
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Room type amenities table
CREATE POLICY blazer_room_type_amenities_policy ON room_type_amenities
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Services table
CREATE POLICY blazer_services_policy ON services
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Service schedules table
CREATE POLICY blazer_service_schedules_policy ON service_schedules
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Service bookings table
CREATE POLICY blazer_service_bookings_policy ON service_bookings
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Housekeeping tasks table
CREATE POLICY blazer_housekeeping_tasks_policy ON housekeeping_tasks
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Maintenance requests table
CREATE POLICY blazer_maintenance_requests_policy ON maintenance_requests
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Cancellation policies table
CREATE POLICY blazer_cancellation_policies_policy ON cancellation_policies
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- Hotel policies table
CREATE POLICY blazer_hotel_policies_policy ON hotel_policies
    FOR SELECT TO blazer_readonly
    USING (account_id = current_setting('blazer.account_id', true)::bigint);

-- 7. Grant access to Blazer's own tables (no RLS needed)
GRANT ALL ON blazer_queries TO blazer_readonly;
GRANT ALL ON blazer_audits TO blazer_readonly;
GRANT ALL ON blazer_dashboards TO blazer_readonly;
GRANT ALL ON blazer_dashboard_queries TO blazer_readonly;
GRANT ALL ON blazer_checks TO blazer_readonly;

-- Grant sequence permissions for Blazer tables
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO blazer_readonly;

-- 8. Create a function to set the account context
CREATE OR REPLACE FUNCTION set_blazer_account_context(account_id bigint)
RETURNS void AS $$
BEGIN
    PERFORM set_config('blazer.account_id', account_id::text, false);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant execute permission on the function
GRANT EXECUTE ON FUNCTION set_blazer_account_context(bigint) TO blazer_readonly;

-- Note: To test this setup, connect as blazer_readonly user and run:
-- SELECT set_blazer_account_context(1); -- Replace 1 with an actual account_id
-- SELECT * FROM hotels; -- Should only show hotels for account_id = 1