require 'csv'

class Admin::StudentsController < Admin::BaseController
  before_action :set_student_account, only: [:show, :edit, :update, :destroy, :regenerate_api_key]

  def index
    # Find accounts that have a student user (non-AI, non-platform-admin)
    @student_accounts = Account.joins(account_users: :user)
                               .where(users: { is_ai_agent: false, is_platform_admin: false })
                               .where.not(accounts: { subdomain: 'demo' })
                               .distinct
                               .includes(:hotels)
                               .order(:created_at)
  end

  def show
    @hotel = @account.hotels.first
    @student_user = student_user_for_account(@account)
    @student_account_user = @account.account_users.find_by(user: @student_user) if @student_user
    @api_key = student_api_key(@account)
  end

  def new
  end

  def create
    email = params[:email].to_s.strip.downcase

    if email.blank?
      flash.now[:alert] = "Email is required."
      render :new, status: :unprocessable_entity
      return
    end

    if User.exists?(email: email)
      flash.now[:alert] = "A user with this email already exists."
      render :new, status: :unprocessable_entity
      return
    end

    result = create_student_account(email)

    if result[:success]
      flash[:notice] = "Student account created for #{email}."
      redirect_to admin_student_path(result[:account])
    else
      flash.now[:alert] = result[:error]
      render :new, status: :unprocessable_entity
    end
  end

  def edit
  end

  def update
    hotel = @account.hotels.first
    hotel&.update!(name: params[:hotel_name]) if params[:hotel_name].present?

    flash[:notice] = "Student account updated."
    redirect_to admin_student_path(@account)
  rescue ActiveRecord::RecordInvalid => e
    flash.now[:alert] = e.message
    render :edit, status: :unprocessable_entity
  end

  def destroy
    student_user = student_user_for_account(@account)
    email = student_user&.email || @account.subdomain

    # Delete the student user
    student_user&.destroy

    # Delete the account (cascades to hotel, rooms, etc.)
    @account.destroy!

    flash[:notice] = "Student account for '#{email}' deleted."
    redirect_to admin_students_path
  end

  def bulk_new
  end

  def bulk_results
    @results = session.delete(:bulk_results)
    redirect_to admin_students_path, alert: "No results found." unless @results
  end

  def bulk_create
    entries_text = params[:emails].to_s.strip

    if entries_text.blank?
      flash[:alert] = "Please enter at least one entry."
      redirect_to bulk_new_admin_students_path
      return
    end

    results = { created: [], errors: [] }

    begin
      rows = CSV.parse(entries_text).reject { |row| row.empty? || row.all?(&:blank?) }

      # Validate all rows first
      parsed_rows = []
      rows.each do |row|
        name, email, student_id = row.map { |v| v&.strip }

        if name.blank? || email.blank? || student_id.blank?
          results[:errors] << "#{row.join(',')}: invalid format (expected name,email,student_id)"
          next
        end

        email = email.downcase

        if User.exists?(email: email)
          results[:errors] << "#{email}: already exists"
          next
        end

        # Parse name: "Baker, Bob L." -> last_name: "Baker", first_name: "Bob L."
        name_parts = name.split(',', 2).map(&:strip)
        last_name = name_parts[0] || ""
        first_name = name_parts[1] || ""

        parsed_rows << { email: email, student_id: student_id, first_name: first_name, last_name: last_name }
      end

      # If any validation errors, don't create anything
      if results[:errors].any?
        session[:bulk_results] = {
          created_count: 0,
          error_count: results[:errors].size,
          errors_sample: results[:errors].first(5)
        }
        redirect_to bulk_results_admin_students_path
        return
      end

      # Create all in one transaction
      Account.transaction do
        parsed_rows.each do |row|
          password = "ai_#{row[:student_id]}"
          result = create_student_account(row[:email], password, first_name: row[:first_name], last_name: row[:last_name])
          if result[:success]
            results[:created] << row[:email]
          else
            results[:errors] << "#{row[:email]}: #{result[:error]}"
            raise ActiveRecord::Rollback
          end
        end
      end

      # Check if transaction was rolled back
      if results[:errors].any?
        results[:created] = []  # Clear created since they were rolled back
      end
    rescue CSV::MalformedCSVError => e
      flash[:alert] = "CSV parsing error: #{e.message}"
      redirect_to bulk_new_admin_students_path
      return
    end

    session[:bulk_results] = {
      created_count: results[:created].size,
      error_count: results[:errors].size,
      errors_sample: results[:errors].first(5)
    }
    redirect_to bulk_results_admin_students_path
  end

  def regenerate_api_key
    ai_agent_user = User.find_by(email: "ai-agent@system.local")
    account_user = @account.account_users.find_by(user: ai_agent_user)

    if account_user
      new_key = generate_api_key(@account.id)
      account_user.update!(api_key: new_key)
      flash[:notice] = "API key regenerated."
    else
      flash[:alert] = "No AI agent found for this account."
    end

    redirect_to admin_student_path(@account)
  end

  private

  def set_student_account
    @account = Account.find(params[:id])
  end

  def create_student_account(email, password = nil, first_name: nil, last_name: nil)
    # Generate a simple password if not provided
    password ||= generate_simple_password

    # Generate subdomain from email (take part before @, sanitize)
    subdomain = email.split('@').first.gsub(/[^a-z0-9]/, '').first(20)
    subdomain = "student#{SecureRandom.hex(4)}" if subdomain.blank?

    # Ensure unique subdomain
    base_subdomain = subdomain
    counter = 1
    while Account.exists?(subdomain: subdomain)
      subdomain = "#{base_subdomain}#{counter}"
      counter += 1
    end

    # Default names if not provided
    first_name = subdomain.titleize if first_name.blank?
    last_name = "Student" if last_name.blank?

    Account.transaction do
      # Create account
      account = Account.create!(
        name: "#{subdomain.titleize} Hotel Group",
        subdomain: subdomain,
        address: "123 Hotel Street",
        city: "San Francisco",
        state: "CA",
        country: "USA",
        postal_code: "94102",
        phone: "415-555-0100",
        email: email,
        timezone: "Pacific Time (US & Canada)",
        currency: "USD",
        status: "active"
      )

      # Set tenant context for child records
      hotel = nil
      user = nil
      ActsAsTenant.with_tenant(account) do
        # Create hotel (matching demo seed)
        hotel = create_hotel_for_account(account)

        # Create student user
        user = User.create!(
          email: email,
          first_name: first_name,
          last_name: last_name,
          password: password,
          password_confirmation: password,
          force_password_change: false
        )

        account_user = AccountUser.create!(account: account, user: user, generated_password: password)
        account_user.add_role(:owner)

        # Setup AI agent
        setup_ai_agent_for_account(account)
      end

      { success: true, account: account, hotel: hotel, user: user }
    end
  rescue ActiveRecord::RecordInvalid => e
    { success: false, error: e.message }
  end

  def create_hotel_for_account(account)
    hotel = Hotel.create!(
      account: account,
      name: "#{account.subdomain.titleize} Hotel",
      subdomain: account.subdomain,
      address: "456 Market Street",
      city: "San Francisco",
      state: "CA",
      country: "USA",
      postal_code: "94103",
      phone: "415-555-0200",
      email: "hotel@#{account.subdomain}.example.com",
      website: "https://#{account.subdomain}.example.com",
      description: "Modern hotel in downtown San Francisco",
      timezone: "Pacific Time (US & Canada)",
      star_rating: 4.0,
      total_rooms: 20
    )

    create_room_types_and_rooms(account, hotel)
    create_amenities(account, hotel)
    create_policies(account, hotel)
    create_sample_guest_and_reservation(account, hotel)
    enable_ai(account, hotel)

    hotel
  end

  def create_room_types_and_rooms(account, hotel)
    standard = RoomType.create!(
      account: account,
      hotel: hotel,
      name: "Standard Room",
      code: "STD",
      description: "Comfortable room with city view",
      base_price: 150.00,
      max_occupancy: 2,
      standard_occupancy: 2,
      extra_person_charge: 25.00,
      size_sqm: 25
    )

    deluxe = RoomType.create!(
      account: account,
      hotel: hotel,
      name: "Deluxe Room",
      code: "DLX",
      description: "Spacious room with premium amenities",
      base_price: 250.00,
      max_occupancy: 4,
      standard_occupancy: 2,
      extra_person_charge: 50.00,
      size_sqm: 35
    )

    # Standard rooms 101-110
    (101..110).each do |num|
      Room.create!(
        account: account,
        hotel: hotel,
        room_type: standard,
        room_number: num.to_s,
        floor: "1",
        status: "available"
      )
    end

    # Deluxe rooms 201-210
    (201..210).each do |num|
      Room.create!(
        account: account,
        hotel: hotel,
        room_type: deluxe,
        room_number: num.to_s,
        floor: "2",
        status: "available"
      )
    end
  end

  def create_amenities(account, hotel)
    hotel_amenities = [
      { name: "Free WiFi", category: "hotel", description: "High-speed wireless internet throughout the property" },
      { name: "Swimming Pool", category: "outdoor", description: "Outdoor heated swimming pool" },
      { name: "Fitness Center", category: "wellness", description: "24/7 gym with modern equipment" },
      { name: "Restaurant", category: "hotel", description: "On-site dining serving breakfast, lunch, and dinner" },
      { name: "Bar", category: "hotel", description: "Full-service bar with cocktails and local beers" },
      { name: "Free Parking", category: "hotel", description: "Complimentary self-parking for guests" },
      { name: "Business Center", category: "business", description: "Computer workstations and printing services" },
      { name: "Pet Friendly", category: "hotel", description: "Pets welcome with additional fee" },
      { name: "Wheelchair Accessible", category: "accessibility", description: "Full wheelchair accessibility" }
    ]

    hotel_amenities.each do |data|
      amenity = Amenity.create!(
        account: account,
        name: data[:name],
        category: data[:category],
        description: data[:description],
        is_countable: false
      )
      HotelAmenity.create!(
        account: account,
        hotel: hotel,
        amenity: amenity,
        is_available: true,
        is_free: data[:name] != "Pet Friendly"
      )
    end
  end

  def create_policies(account, hotel)
    return unless defined?(HotelPolicy)

    HotelPolicy.create!([
      {
        account: account, hotel: hotel, policy_type: "check_in",
        title: "Check-in Policy",
        description: "Check-in time is from 3:00 PM. Early check-in may be available upon request, subject to availability. Valid photo ID and credit card required at check-in.",
        sort_order: 1, is_active: true
      },
      {
        account: account, hotel: hotel, policy_type: "check_out",
        title: "Check-out Policy",
        description: "Check-out time is 11:00 AM. Late check-out may be available for an additional fee of $50 per hour, subject to availability.",
        sort_order: 2, is_active: true
      },
      {
        account: account, hotel: hotel, policy_type: "cancellation",
        title: "Cancellation Policy",
        description: "Cancellations made 48 hours or more before check-in are fully refundable. Cancellations made within 48 hours are subject to a one-night charge.",
        sort_order: 3, is_active: true
      },
      {
        account: account, hotel: hotel, policy_type: "pet",
        title: "Pet Policy",
        description: "We welcome pets! A non-refundable fee of $50 per night applies. Maximum 2 pets per room, weight limit 50 lbs each.",
        sort_order: 4, is_active: true
      },
      {
        account: account, hotel: hotel, policy_type: "smoking",
        title: "Non-Smoking Policy",
        description: "This is a 100% smoke-free property. A $250 cleaning fee will be charged for violations.",
        sort_order: 5, is_active: true
      }
    ])
  end

  def create_sample_guest_and_reservation(account, hotel)
    guest = Guest.create!(
      account: account,
      first_name: "John",
      last_name: "Smith",
      email: "john.smith@example.com",
      phone: "555-0123",
      date_of_birth: Date.new(1985, 6, 15),
      address: "789 Oak Street",
      city: "San Francisco",
      state: "CA",
      country: "USA",
      postal_code: "94110"
    )

    room = hotel.rooms.first
    Reservation.create!(
      account: account,
      hotel: hotel,
      guest: guest,
      room: room,
      room_type: room.room_type,
      check_in_date: 2.days.from_now.to_date,
      check_out_date: 4.days.from_now.to_date,
      status: "confirmed",
      adults: 2,
      children: 0,
      infants: 0,
      total_amount: 300.00,
      paid_amount: 300.00,
      payment_status: "paid",
      source: "website"
    )
  end

  def enable_ai(account, hotel)
    return unless defined?(HotelAiSetting)

    HotelAiSetting.create!(
      account: account,
      hotel: hotel,
      deployment_key: 'web_chat',
      enabled: true,
      settings: {}
    )
  end

  def setup_ai_agent_for_account(account)
    ai_agent_user = User.find_or_create_by!(email: "ai-agent@system.local") do |u|
      u.first_name = "AI"
      u.last_name = "Agent"
      u.password = SecureRandom.hex(32)
      u.is_ai_agent = true
    end

    api_key = generate_api_key(account.id)
    account_user = AccountUser.create!(
      account: account,
      user: ai_agent_user,
      api_key: api_key
    )
    account_user.add_role(:admin)
  end

  def generate_api_key(account_id)
    "ak_#{account_id}_#{SecureRandom.hex(8)}"
  end

  def generate_simple_password
    # Simple memorable passwords like: Hotel@123, Sunny#456, Beach!789
    words = %w[Hotel Sunny Beach Ocean River Mountain Forest Garden Sunset Sunrise]
    symbols = %w[! @ # $]
    "#{words.sample}#{symbols.sample}#{rand(100..999)}"
  end

  def student_user_for_account(account)
    account.users.where(is_ai_agent: false, is_platform_admin: false).first
  end

  def student_api_key(account)
    ai_agent_user = User.find_by(email: "ai-agent@system.local")
    return nil unless ai_agent_user

    account_user = account.account_users.find_by(user: ai_agent_user)
    account_user&.api_key
  end
end
