# frozen_string_literal: true

module Public
  class ReservationsController < BaseController
    before_action :set_hotel_from_subdomain, only: [:lookup, :search]
    before_action :set_and_verify_reservation, only: [:show, :edit, :update, :cancel, :confirm_cancel]
    
    def show
      # @reservation and @hotel are set by before_action
      @cancellation_policy = @hotel.cancellation_policies.find_by(is_default: true) || @hotel.cancellation_policies.first
    end
    
    def lookup
      # Form to look up reservation
    end
    
    def search
      @reservation = find_reservation
      
      if @reservation
        redirect_to public_reservation_path(@reservation.confirmation_number, 
                                           email: params[:email], 
                                           phone: params[:phone])
      else
        redirect_to public_reservation_lookup_path, alert: 'Reservation not found. Please check your details.'
      end
    end
    
    def edit
      # Check if reservation is modifiable
      unless @reservation.can_modify?
        redirect_to public_reservation_path(@reservation.confirmation_number, 
                                           email: params[:email], 
                                           phone: params[:phone]),
                    alert: 'This reservation can no longer be modified.'
        return
      end
      
      # Set up date and guest variables for the form
      @check_in_date = @reservation.check_in_date
      @check_out_date = @reservation.check_out_date
      @guests = @reservation.adults
      @room_types = @hotel.room_types
    end
    
    def update
      # Check if reservation is modifiable
      unless @reservation.can_modify?
        redirect_to public_reservation_path(@reservation.confirmation_number, 
                                           email: params[:email], 
                                           phone: params[:phone]),
                    alert: 'This reservation can no longer be modified.'
        return
      end
      
      # Store original values for comparison
      original_dates = [@reservation.check_in_date, @reservation.check_out_date]
      original_room_type = @reservation.room_type
      
      # Update reservation within account context
      ActsAsTenant.with_tenant(@reservation.account) do
        # Parse new dates and room type
        new_check_in = Date.parse(params[:reservation][:check_in_date]) rescue @reservation.check_in_date
        new_check_out = Date.parse(params[:reservation][:check_out_date]) rescue @reservation.check_out_date
        new_room_type_id = params[:reservation][:room_type_id]
        new_adults = params[:reservation][:adults].to_i
        
        # Check availability for new dates/room type
        if new_room_type_id.present?
          new_room_type = @hotel.room_types.find(new_room_type_id)
          available_count = new_room_type.available_rooms_count(new_check_in, new_check_out)
          
          # Add 1 to available count if changing dates but keeping same room type (releasing current reservation)
          available_count += 1 if new_room_type_id == @reservation.room_type_id.to_s
          
          if available_count <= 0
            redirect_to edit_public_reservation_path(@reservation.confirmation_number,
                                                    email: params[:email],
                                                    phone: params[:phone]),
                        alert: 'No availability for the selected dates and room type.'
            return
          end
        end
        
        # Update reservation
        @reservation.check_in_date = new_check_in
        @reservation.check_out_date = new_check_out
        @reservation.room_type_id = new_room_type_id if new_room_type_id.present?
        @reservation.adults = new_adults
        @reservation.children = params[:reservation][:children].to_i
        @reservation.special_requests = params[:reservation][:special_requests]
        
        # Recalculate total amount
        nights = (new_check_out - new_check_in).to_i
        @reservation.total_amount = @reservation.room_type.calculate_price(nights: nights, guests: new_adults)
        
        # Clear room assignment if dates or room type changed
        if original_dates != [new_check_in, new_check_out] || original_room_type != @reservation.room_type
          @reservation.room_id = nil
        end
        
        if @reservation.save
          # Log the modification
          @reservation.internal_notes = "#{@reservation.internal_notes}\n[#{DateTime.current}] Guest modified reservation online"
          @reservation.save
          
          # Send modification confirmation email
          ReservationMailer.modification_confirmation(@reservation).deliver_later
          
          redirect_to public_reservation_path(@reservation.confirmation_number,
                                             email: params[:email],
                                             phone: params[:phone]),
                      notice: 'Your reservation has been successfully updated.'
        else
          render :edit, status: :unprocessable_content
        end
      end
    end
    
    def cancel
      # Show cancellation confirmation page
      unless @reservation.can_cancel?
        redirect_to public_reservation_path(@reservation.confirmation_number,
                                           email: params[:email],
                                           phone: params[:phone]),
                    alert: 'This reservation can no longer be cancelled online. Please contact the hotel.'
        return
      end
      
      # Calculate cancellation fees if any
      @cancellation_fee = @reservation.calculate_cancellation_fee
      @refund_amount = @reservation.paid_amount - @cancellation_fee
      @cancellation_policy = @hotel.cancellation_policies.find_by(is_default: true) || @hotel.cancellation_policies.first
    end
    
    def confirm_cancel
      # Process the cancellation
      unless @reservation.can_cancel?
        redirect_to public_reservation_path(@reservation.confirmation_number,
                                           email: params[:email],
                                           phone: params[:phone]),
                    alert: 'This reservation can no longer be cancelled online. Please contact the hotel.'
        return
      end
      
      ActsAsTenant.with_tenant(@reservation.account) do
        # Update reservation status
        @reservation.status = 'cancelled'
        @reservation.internal_notes = "#{@reservation.internal_notes}\n[#{DateTime.current}] Guest cancelled online"
        
        if @reservation.save
          # Send cancellation confirmation email
          ReservationMailer.cancellation_confirmation(@reservation).deliver_later
          
          # TODO: Process refund if payment was collected
          
          redirect_to cancelled_public_reservation_path(@reservation.confirmation_number,
                                                       email: params[:email],
                                                       phone: params[:phone])
        else
          redirect_to public_reservation_path(@reservation.confirmation_number,
                                             email: params[:email],
                                             phone: params[:phone]),
                      alert: 'Unable to cancel reservation. Please contact the hotel.'
        end
      end
    end
    
    def cancelled
      # Show cancellation confirmation
      @reservation = find_reservation
      if @reservation && @reservation.status == 'cancelled'
        @hotel = @reservation.hotel
      else
        # Set hotel from subdomain/params for the redirect path
        set_hotel_from_subdomain
        redirect_to public_reservation_lookup_path, alert: 'Reservation not found.'
      end
    end
    
    private

    def set_and_verify_reservation
      @reservation = find_reservation

      if @reservation
        @hotel = @reservation.hotel
      else
        # Set hotel from subdomain/params for the redirect path
        set_hotel_from_subdomain
        redirect_to public_reservation_lookup_path, alert: 'Reservation not found. Please check your details.'
      end
    end
    
    def find_reservation
      reservation = Reservation.find_by(confirmation_number: params[:id] || params[:confirmation_number])
      
      # Verify with email or phone for security
      if reservation && (params[:email].present? || params[:phone].present?)
        guest = reservation.guest
        
        if params[:email].present? && guest.email&.downcase == params[:email]&.downcase
          reservation
        elsif params[:phone].present? && guest.phone == params[:phone]
          reservation
        else
          nil
        end
      else
        nil
      end
    end
  end
end