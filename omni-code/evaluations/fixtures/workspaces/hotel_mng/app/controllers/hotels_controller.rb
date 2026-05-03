# frozen_string_literal: true

class HotelsController < StaffController
  before_action :set_hotel, only: [:show, :edit, :update, :destroy]
  
  def index
    @hotels = policy_scope(Hotel)
    authorize Hotel
  end
  
  def show
    authorize @hotel
  end
  
  def new
    @hotel = current_tenant.hotels.build
    authorize @hotel
  end
  
  def create
    @hotel = current_tenant.hotels.build(hotel_params)
    authorize @hotel
    
    if @hotel.save
      # Set as current hotel if it's the only one or no current hotel
      set_current_hotel(@hotel.id) if current_tenant.hotels.count == 1 || current_hotel.nil?
      redirect_to @hotel, notice: 'Hotel was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @hotel
  end
  
  def update
    authorize @hotel
    if @hotel.update(hotel_params)
      # Handle AI settings if present
      update_ai_settings if params[:ai_settings].present?
      
      redirect_to @hotel, notice: 'Hotel was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @hotel
    
    # Check if this is the current hotel
    was_current = (@hotel == current_hotel)
    
    # Check if hotel has any reservations
    if @hotel.reservations.exists?
      redirect_to hotels_path, alert: 'Cannot delete hotel with existing reservations.'
    else
      @hotel.destroy
      
      # If we deleted the current hotel, set a new one
      if was_current
        new_current = current_tenant.hotels.first
        set_current_hotel(new_current.id) if new_current
      end
      
      redirect_to hotels_path, notice: 'Hotel was successfully deleted.'
    end
  end
  
  def set_current
    hotel_id = params[:hotel_id]
    
    if set_current_hotel(hotel_id)
      respond_to do |format|
        format.html { 
          redirect_back(fallback_location: authenticated_root_path, notice: "Switched to #{current_hotel.name}") 
        }
        format.turbo_stream { 
          # For Turbo, we'll redirect which will cause a full page refresh
          # This ensures all hotel-specific content is updated
          redirect_to request.referer || authenticated_root_path, status: :see_other
        }
        format.json { render json: { success: true, hotel_id: hotel_id } }
      end
    else
      respond_to do |format|
        format.html { 
          redirect_back(fallback_location: authenticated_root_path, alert: 'Hotel not found') 
        }
        format.json { render json: { success: false, error: 'Hotel not found' }, status: :not_found }
      end
    end
  end
  
  private
  
  def set_hotel
    @hotel = Hotel.find(params[:id])
  end
  
  def hotel_params
    params.require(:hotel).permit(
      :name, :subdomain, :address, :city, :state, :country, :postal_code,
      :phone, :email, :website, :description, :check_in_time, :check_out_time,
      :timezone, :star_rating
    )
  end
  
  def update_ai_settings
    params[:ai_settings].each do |deployment_key, enabled|
      setting = @hotel.hotel_ai_settings.find_or_initialize_by(
        deployment_key: deployment_key,
        account: current_tenant
      )
      setting.enabled = (enabled == "1")
      setting.save
    end
  end
end