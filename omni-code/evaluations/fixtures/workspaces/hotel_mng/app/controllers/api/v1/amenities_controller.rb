class Api::V1::AmenitiesController < Api::V1::BaseController
  before_action :set_amenity, only: [:show, :update, :destroy]

  # GET /api/v1/amenities
  def index
    authorize Amenity
    amenities = policy_scope(@current_account.amenities)
    
    # Apply category filter if provided
    if params[:category].present?
      amenities = amenities.where(category: params[:category])
    end
    
    # Apply search filter if provided
    if params[:search].present?
      amenities = amenities.where("name ILIKE ?", "%#{params[:search]}%")
    end
    
    @pagy, @amenities = pagy(amenities.order(:category, :name))
    
    render json: {
      data: AmenityBlueprint.render_as_hash(@amenities),
      meta: pagination_meta(@pagy)
    }
  end

  # GET /api/v1/amenities/:id
  def show
    authorize @amenity
    
    render json: {
      data: AmenityBlueprint.render_as_hash(@amenity)
    }
  end

  # POST /api/v1/amenities
  def create
    @amenity = @current_account.amenities.build(amenity_params)
    authorize @amenity
    
    if @amenity.save
      render json: {
        data: AmenityBlueprint.render_as_hash(@amenity)
      }, status: :created
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@amenity))
    end
  end

  # PATCH/PUT /api/v1/amenities/:id
  def update
    authorize @amenity
    
    if @amenity.update(amenity_params)
      render json: {
        data: AmenityBlueprint.render_as_hash(@amenity)
      }
    else
      handle_validation_error(ActiveRecord::RecordInvalid.new(@amenity))
    end
  end

  # DELETE /api/v1/amenities/:id
  def destroy
    authorize @amenity
    
    begin
      @amenity.destroy!
      head :no_content
    rescue ActiveRecord::InvalidForeignKey
      render json: {
        error: 'Conflict',
        message: 'Cannot delete amenity that is in use'
      }, status: :conflict
    end
  end

  private

  def set_amenity
    @amenity = @current_account.amenities.find(params[:id])
  rescue ActiveRecord::RecordNotFound
    render_not_found('Amenity')
  end

  def amenity_params
    params.permit(:name, :category, :icon, :description, :is_countable)
  end
end