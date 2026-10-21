# frozen_string_literal: true

module Api
  class DocsController < ActionController::Base
    # No authentication needed for API documentation
    layout false

    def index
      # Serve the Swagger UI interface
      render html: swagger_ui_html.html_safe
    end

    def openapi
      # Serve the OpenAPI specification file
      render file: Rails.root.join('api/openapi.yaml'), 
             content_type: 'text/yaml',
             layout: false
    end

    private

    def swagger_ui_html
      <<~HTML
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <title>Hotel Management API - Swagger UI</title>
          <link rel="stylesheet" type="text/css" href="/api/docs/swagger-ui.css" />
          <link rel="icon" type="image/png" href="/api/docs/favicon-32x32.png" sizes="32x32" />
          <link rel="icon" type="image/png" href="/api/docs/favicon-16x16.png" sizes="16x16" />
          <style>
            html {
              box-sizing: border-box;
              overflow: -moz-scrollbars-vertical;
              overflow-y: scroll;
            }
            *, *:before, *:after {
              box-sizing: inherit;
            }
            body {
              margin:0;
              background: #fafafa;
            }
            .topbar {
              display: none;
            }
            .swagger-ui .info {
              margin: 50px 0;
            }
            .swagger-ui .info .title {
              color: #3b4151;
            }
          </style>
        </head>
        <body>
          <div id="swagger-ui"></div>
          <script src="/api/docs/swagger-ui-bundle.js" charset="UTF-8"></script>
          <script src="/api/docs/swagger-ui-standalone-preset.js" charset="UTF-8"></script>
          <script>
            window.onload = function() {
              // Build a system
              const ui = SwaggerUIBundle({
                url: "/api/docs/openapi.yaml",
                dom_id: '#swagger-ui',
                deepLinking: true,
                presets: [
                  SwaggerUIBundle.presets.apis,
                  SwaggerUIStandalonePreset
                ],
                plugins: [
                  SwaggerUIBundle.plugins.DownloadUrl
                ],
                layout: "StandaloneLayout",
                defaultModelsExpandDepth: 1,
                defaultModelExpandDepth: 1,
                docExpansion: "list",
                filter: true,
                showExtensions: true,
                showCommonExtensions: true,
                tryItOutEnabled: true,
                supportedSubmitMethods: ['get', 'post', 'put', 'delete', 'patch'],
                onComplete: function() {
                  console.log("Swagger UI loaded successfully");
                }
              });
              window.ui = ui;
            }
          </script>
        </body>
        </html>
      HTML
    end
  end
end