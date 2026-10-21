module ApiPagination
  extend ActiveSupport::Concern
  include Pagy::Backend

  private

  def paginate(scope)
    pagy, records = pagy(scope, limit: per_page, page: current_page)
    
    {
      records: records,
      pagy: pagy
    }
  end

  def render_paginated(records, serializer, view: :default)
    result = paginate(records)
    
    render json: {
      data: serializer.render_as_hash(result[:records], view: view),
      meta: pagination_meta(result[:pagy]),
      links: pagination_links(result[:pagy])
    }
  end

  def pagination_meta(pagy)
    {
      current_page: pagy.page,
      total_pages: pagy.pages,
      total_count: pagy.count,
      per_page: pagy.limit,
      from: pagy.from,
      to: pagy.to
    }
  end

  def pagination_links(pagy)
    base_url = request.base_url + request.path
    
    {
      self: build_page_url(base_url, pagy.page),
      first: build_page_url(base_url, 1),
      last: build_page_url(base_url, pagy.pages),
      prev: pagy.prev ? build_page_url(base_url, pagy.prev) : nil,
      next: pagy.next ? build_page_url(base_url, pagy.next) : nil
    }.compact
  end

  def build_page_url(base_url, page)
    uri = URI.parse(base_url)
    params = Rack::Utils.parse_query(uri.query)
    params['page'] = page.to_s
    uri.query = params.to_query
    uri.to_s
  end

  def current_page
    (params[:page] || 1).to_i
  end

  def per_page
    [(params[:per_page] || 25).to_i, 100].min
  end
end