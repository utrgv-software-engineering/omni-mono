# frozen_string_literal: true

class AmenitiesController < StaffController
  before_action :set_amenity, only: [:show, :edit, :update, :destroy]
  
  def index
    @amenities = policy_scope(Amenity)
    authorize Amenity
    
    if params[:category].present?
      @amenities = @amenities.where(category: params[:category])
    end
    
    @amenities = @amenities.order(:category, :name)
  end
  
  def show
    authorize @amenity
  end
  
  def new
    @amenity = Amenity.new
    authorize @amenity
  end
  
  def create
    @amenity = Amenity.new(amenity_params)
    authorize @amenity
    
    if @amenity.save
      redirect_to @amenity, notice: 'Amenity was successfully created.'
    else
      render :new, status: :unprocessable_content
    end
  end
  
  def edit
    authorize @amenity
  end
  
  def update
    authorize @amenity
    if @amenity.update(amenity_params)
      redirect_to @amenity, notice: 'Amenity was successfully updated.'
    else
      render :edit, status: :unprocessable_content
    end
  end
  
  def destroy
    authorize @amenity
    @amenity.destroy
    redirect_to amenities_path, notice: 'Amenity was successfully destroyed.'
  end
  
  private
  
  def set_amenity
    @amenity = Amenity.find(params[:id])
  end
  
  def amenity_params
    params.require(:amenity).permit(:name, :category, :icon, :description, :is_countable)
  end
end