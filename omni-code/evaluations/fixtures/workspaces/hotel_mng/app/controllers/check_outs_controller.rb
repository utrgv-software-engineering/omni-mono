# frozen_string_literal: true

class CheckOutsController < StaffController
  before_action :set_reservation
  
  def new
    authorize @reservation, :check_out?
    
    # Calculate any pending charges
    @pending_charges = calculate_pending_charges
    @balance_due = @reservation.total_amount - @reservation.paid_amount
  end
  
  def create
    authorize @reservation, :check_out?
    
    # Update reservation status and check-out time
    @reservation.status = 'checked_out'
    @reservation.actual_check_out = Time.current
    
    # Process payment if needed
    if params[:payment_amount].present?
      payment_amount = params[:payment_amount].to_f
      @reservation.paid_amount += payment_amount
      @reservation.payment_status = @reservation.paid_amount >= @reservation.total_amount ? 'paid' : 'partial'
    end
    
    # Update room status
    if @reservation.room
      @reservation.room.status = 'cleaning'
      @reservation.room.save
    end
    
    if @reservation.save
      redirect_to @reservation, notice: 'Guest successfully checked out.'
    else
      @pending_charges = calculate_pending_charges
      @balance_due = @reservation.total_amount - @reservation.paid_amount
      render :new, status: :unprocessable_content
    end
  end
  
  private
  
  def set_reservation
    @reservation = Reservation.find(params[:reservation_id])
  end
  
  def calculate_pending_charges
    # This would calculate minibar, services, late checkout fees etc.
    # For now, return empty array
    []
  end
end