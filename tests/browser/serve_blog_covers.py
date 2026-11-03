"""Temporary blog fixture, fake images, no production integrations."""
from serve_blog_icons import app
from PIL import Image,ImageDraw
for fmt,size in [('PNG',(1200,675)),('JPEG',(600,900)),('WEBP',(1200,675))]:
    image=Image.new('RGB',size,'white');draw=ImageDraw.Draw(image)
    width,height=size
    for x,y,color in [(0,0,'orange'),(width-80,0,'teal'),(0,height-80,'black'),(width-80,height-80,'red')]:draw.rectangle((x,y,x+79,y+79),fill=color)
    draw.text((width//3,height//2),'CAPA COMPLETA - TESTE',fill='black')
    image.save('/tmp/blog-cover-fixture.'+fmt.lower(),fmt)
if __name__=='__main__':app.run(host='127.0.0.1',port=5006)
