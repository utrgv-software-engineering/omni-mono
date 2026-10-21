# frozen_string_literal: true

class HotelResolver
  def initialize(app)
    @app = app
  end

  def call(env)
    request = ActionDispatch::Request.new(env)
    
    # Skip API routes entirely
    return @app.call(env) if request.path.start_with?('/api/')

    # Skip if not a public route
    # Check if path starts with /hotels/ and has a non-numeric slug (not /hotels/5/edit)
    # Also skip standard Rails resource routes (new, create, etc.) which are handled by staff controllers
    is_hotel_slug_path = request.path.match?(%r{^/hotels/([^/]+)}) &&
                         !request.path.match?(%r{^/hotels/\d+}) &&
                         !request.path.match?(%r{^/hotels/(new|create)(/|$)})
    
    if request.path.start_with?('/public') || is_hotel_slug_path
      hotel = resolve_hotel(request)
      
      if hotel
        # Store hotel in request env for controllers to access
        env['hotel.current'] = hotel
        
        # For path-based routing, rewrite the path to remove /hotels/{slug}
        if request.path.match?(%r{^/hotels/([^/]+)(.*)})
          slug, remaining_path = request.path.match(%r{^/hotels/([^/]+)(.*)}).captures
          # Rewrite path to public namespace
          env['PATH_INFO'] = "/public#{remaining_path.presence || '/hotel'}"
          env['REQUEST_PATH'] = env['PATH_INFO'] if env['REQUEST_PATH']
          env['REQUEST_URI'] = env['PATH_INFO'] if env['REQUEST_URI']
        end
      elsif is_hotel_slug_path
        # Hotel slug path but hotel not found
        return [302, {'Location' => '/', 'Content-Type' => 'text/html'}, ['Hotel not found']]
      end
    end
    
    @app.call(env)
  end
  
  private
  
  def resolve_hotel(request)
    # Priority 1: Path-based (for development)
    if request.path.match?(%r{^/hotels/([^/]+)})
      slug = request.path.match(%r{^/hotels/([^/]+)})[1]
      return Hotel.find_by(subdomain: slug)
    end
    
    # Priority 2: Subdomain-based (for production)
    if request.subdomain.present? && request.subdomain != 'www'
      return Hotel.find_by(subdomain: request.subdomain)
    end
    
    nil
  end
end