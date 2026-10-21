Rails.application.routes.draw do
  devise_for :users, controllers: {
    registrations: 'users/registrations',
    sessions: 'users/sessions'
  }

  # Password change (for forced password change on first login)
  resource :password_change, only: [:edit, :update]
  constraints Constraints::PlatformAdminConstraint do
    mount MissionControl::Jobs::Engine, at: "/jobs"
  end
  mount Mailbin::Engine => :mailbin if Rails.env.development?
  
  # Define your application routes per the DSL in https://guides.rubyonrails.org/routing.html

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  # Can be used by load balancers and uptime monitors to verify that the app is live.
  get "up" => "rails/health#show", as: :rails_health_check

  # Render dynamic PWA files from app/views/pwa/* (remember to link manifest in application.html.erb)
  # get "manifest" => "rails/pwa#manifest", as: :pwa_manifest
  # get "service-worker" => "rails/pwa#service_worker", as: :pwa_service_worker

  # Design system/kitchen sink (accessible without authentication)
  namespace :design do
    get 'system', to: 'system#index'
    get 'components', to: 'system#components'
  end

  # Platform admin routes (only accessible by platform admins)
  namespace :admin do
    root to: 'students#index'
    resources :students do
      member do
        post :regenerate_api_key
      end
      collection do
        get :bulk_new
        post :bulk_create
        get :bulk_results
      end
    end
  end
  
  # API routes for AI agents
  namespace :api do
    # API Documentation
    get 'docs', to: 'docs#index'
    get 'docs/openapi.yaml', to: 'docs#openapi'
    
    namespace :v1 do
      # Hotels management (Phase 2)
      resources :hotels do
        # Hotel-scoped endpoints
        resources :rooms do
          member do
            patch :status, to: 'rooms#update_status'
          end
        end
        resources :room_types
        get :availability, to: 'availability#index'
        resources :reservations, only: [:index, :create]
      end
      
      # Standalone reservation endpoints (Phase 2: expanded)
      resources :reservations, only: [:show, :update, :destroy] do
        collection do
          get :lookup
        end
        # Check-in/out workflow endpoints (using nested controllers)
        resource :check_in, only: [:create], controller: 'check_ins' do
          get :available_rooms
        end
        resource :check_out, only: [:create], controller: 'check_outs' do
          get :summary
        end
      end
      
      # Guests management (Phase 2)
      resources :guests do
        member do
          patch :vip_status, to: 'guests#update_vip_status'
        end
      end
      
      # Phase 3: Amenities & Policies
      # Amenities master list (account-wide)
      resources :amenities
      
      # Cancellation policies (can be account-wide or hotel-specific)
      resources :cancellation_policies do
        member do
          post :make_default
        end
        collection do
          post :calculate_fee
        end
      end
      
      # Hotel-nested amenities and policies endpoints
      resources :hotels, only: [] do
        # Hotel amenities (specific amenities offered by hotel)
        resources :amenities, controller: 'hotel_amenities'
        
        # Hotel policies (check-in/out, pet policies, etc)
        resources :policies, controller: 'hotel_policies' do
          member do
            post :activate
            post :deactivate
          end
        end
        
        # Hotel-specific cancellation policies
        resources :cancellation_policies, only: [:index, :create]
        
        # Room Type amenities (nested under hotel for consistency)
        resources :room_types, only: [] do
          resources :amenities, controller: 'room_type_amenities'
        end
        
        # Phase 4: Housekeeping Tasks
        resources :housekeeping_tasks do
          member do
            put :assign
            put :start
            put :complete
          end
        end
        
        # Phase 5: Maintenance Requests
        resources :maintenance_requests do
          member do
            put :assign
            put :start
            put :complete
            put :cancel
          end
        end
      end
      
      # Data Agent endpoints for analytics
      get 'data_agent/tables', to: 'data_agent#tables'
      get 'data_agent/tables/:table_name', to: 'data_agent#describe_table'
      post 'data_agent/preview_query', to: 'data_agent#preview_query'

      # Evaluation API endpoints (test environment only)
      post 'eval/setup', to: 'eval#setup'
      post 'eval/verify', to: 'eval#verify'
      post 'eval/query', to: 'eval#query'
      delete 'eval/teardown', to: 'eval#teardown'
    end
  end

  # Public/Consumer-facing routes (no authentication required)
  namespace :public do
    # Hotel public page (main entry point)
    resource :hotel, only: [:show]
    
    # Room browsing and details
    resources :rooms, only: [:index, :show]
    
    # Room availability search
    resources :availability, only: [:index]
    
    # Booking flow
    resources :bookings, only: [:new, :create, :show]
    
    # Reservation lookup
    get 'reservations/lookup', to: 'reservations#lookup', as: :reservation_lookup
    post 'reservations/search', to: 'reservations#search', as: :reservation_search
    resources :reservations, only: [:show, :edit, :update] do
      member do
        get :cancel
        post :confirm_cancel
        get :cancelled
      end
    end
  end
  
  # Staff-facing routes (authentication required)
  authenticated :user do
    root to: 'dashboard#show', as: :authenticated_root
    
    # Reports wrapper (iframes Blazer)
    resources :reports, only: [:index]
    
    # Mount Blazer for Business Intelligence (accessed via iframe)
    # Authorization is handled in the initializer
    mount Blazer::Engine, at: "blazer"
    
    # Account switching
    resources :accounts, only: [:index] do
      member do
        post :switch
      end
    end
    
    # Hotel switching (within current account)
    post 'set_current_hotel', to: 'hotels#set_current'
    
    # Dashboard
    resource :dashboard, only: [:show]
    
    # Hotels management
    resources :hotels do
      # Nested room types under hotels
      resources :room_types do
        # Room type amenities (deep nested)
        resources :room_type_amenities, only: [:index, :create, :destroy]
      end
      
      # Nested rooms under hotels
      resources :rooms
      
      # Nested hotel policies under hotels
      resources :hotel_policies
      
      # Nested hotel amenities under hotels
      resources :hotel_amenities
      
      # Housekeeping tasks under hotels
      resources :housekeeping_tasks do
        member do
          patch :mark_complete
          patch :assign
        end
      end
      
      # Maintenance requests under hotels
      resources :maintenance_requests do
        member do
          patch :assign
          patch :complete
          patch :cancel
        end
      end
      
      # Services under hotels
      resources :services do
        resources :service_schedules
      end
    end
    
    # Amenities (account-wide master list)
    resources :amenities
    
    # Cancellation policies (account-wide or hotel-specific)
    resources :cancellation_policies
    
    # Guests management
    resources :guests
    
    # Reservations management
    resources :reservations do
      # Check-in/out actions
      resource :check_in, only: [:new, :create]
      resource :check_out, only: [:new, :create]
      # Service bookings nested under reservations
      resources :service_bookings
    end
    
    # Service bookings (standalone for direct booking)
    resources :service_bookings
    
    # Account users management
    resources :account_users
  end
  
  # Root route for unauthenticated users
  root to: redirect('/users/sign_in')
end
