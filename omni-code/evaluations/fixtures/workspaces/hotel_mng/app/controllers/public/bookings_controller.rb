# frozen_string_literal: true

module Public
  class BookingsController < BaseController
    before_action :set_hotel_from_subdomain
    before_action :set_room_type, only: [:new, :create]
    
    def new
      @booking = Reservation.new
      @booking.check_in_date = params[:check_in_date] if params[:check_in_date]
      @booking.check_out_date = params[:check_out_date] if params[:check_out_date]
      @booking.adults = params[:guests]&.to_i || 1
      
      # Calculate total amount
      if @booking.check_in_date && @booking.check_out_date
        nights = (@booking.check_out_date - @booking.check_in_date).to_i
        @booking.total_amount = nights * @room_type.base_price
      end
      
      @guest = Guest.new
      @hotel_policies = @hotel.hotel_policies.where(is_active: true).order(:sort_order)
      @cancellation_policy = @hotel.cancellation_policies.find_by(is_default: true) || @hotel.cancellation_policies.first
    end
    
    def create
      # Create or find guest within hotel's account context
      ActsAsTenant.with_tenant(@hotel.account) do
        @guest = find_or_create_guest
        
        if @guest.persisted?
          @booking = @hotel.reservations.build(booking_params)
          @booking.account = @hotel.account
          @booking.guest = @guest
          @booking.room_type = @room_type
          @booking.confirmation_number = generate_confirmation_number
          @booking.status = 'confirmed'
          @booking.source = 'website'
          @booking.payment_status = 'pending'
          @booking.paid_amount = 0
          @booking.currency = @hotel.account.currency || 'USD'
          
          # Calculate total using room type's pricing method
          nights = (@booking.check_out_date - @booking.check_in_date).to_i
          @booking.total_amount = @room_type.calculate_price(nights: nights, guests: @booking.adults)
          
          # Default children and infants if not provided
          @booking.children ||= 0
          @booking.infants ||= 0
          
          if @booking.save
            # Send confirmation email
            ReservationMailer.booking_confirmation(@booking).deliver_later
            
            redirect_to public_booking_path(@booking.confirmation_number), 
                        notice: 'Your booking has been confirmed!'
          else
            render :new, status: :unprocessable_content
          end
        else
          @booking = Reservation.new(booking_params)
          render :new, status: :unprocessable_content
        end
      end
    end
    
    def show
      @booking = Reservation.find_by!(confirmation_number: params[:id])
      @hotel = @booking.hotel
    end
    
    private
    
    def set_room_type
      @room_type = @hotel.room_types.find(params[:room_type_id])
    end
    
    def find_or_create_guest
      # Find or create guest within the hotel's account scope
      guest = Guest.find_by(email: guest_params[:email], account: @hotel.account)
      
      if guest
        guest.update(guest_params)
        guest
      else
        Guest.create(guest_params.merge(account: @hotel.account))
      end
    end
    
    def booking_params
      params.require(:reservation).permit(
        :check_in_date, :check_out_date, :adults, :children, :infants,
        :special_requests
      )
    end
    
    def guest_params
      params.require(:guest).permit(
        :first_name, :last_name, :email, :phone, :address,
        :city, :state, :country, :postal_code
      )
    end
    
    def generate_confirmation_number
      loop do
        number = SecureRandom.alphanumeric(8).upcase
        break number unless Reservation.exists?(confirmation_number: number)
      end
    end
  end
end