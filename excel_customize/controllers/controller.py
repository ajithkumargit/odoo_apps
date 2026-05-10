# controllers/main.py

from odoo import http
from odoo.http import request



class ExcelProcessor(http.Controller):

    @http.route(
        '/process/excel',
        type='http',
        auth='user',
        methods=['POST'],
        csrf=False
    )
    def process_excel(self, **post):

        uploaded_file = post.get('file')

        if not uploaded_file:
            return request.make_response(
                "No file uploaded",
                headers=[('Content-Type', 'text/plain')]
            )

        output = request.env['excel.data'].customizeExcel({'uploadedFIle':uploaded_file})
        new_filename = output['filename']
        if output.get('status') == 'success' and output.get('message') == 'binary':
            
            output = output['binary']
            return request.make_response(
                output.read(),
                headers=[
                    (
                        'Content-Type',
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    ),
                    (
                        'Content-Disposition',
                        f'attachment; filename={new_filename}'
                    )
                ]
            )
        elif output.get('status') == 'success' and output.get('message') == 'folder':
            path = post.get('save_path','D:/')
            output = output['binary']
            with open(path+'/'+new_filename,'wb') as f:
                f.write(output.read())
            return request.make_response(
                "File saved successfully",
                headers=[('Content-Type', 'text/plain')]
            )
        else:
            return request.make_response(
                "Error: "+output['message'],
                headers=[('Content-Type', 'text/plain')]
            )