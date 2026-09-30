FROM python:3

COPY . .

RUN pip install -r requirements.txt

EXPOSE 8080

CMD [python, app.py]