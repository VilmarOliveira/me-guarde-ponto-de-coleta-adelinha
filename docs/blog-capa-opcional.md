# Capa opcional nos artigos do blog

Em **Área do Ponto → Blog → Criar ou editar artigo**, o campo **Imagem de capa — opcional** está disponível tanto no modelo com ícones como no anterior.

As orientações visíveis são: **1200 × 675 px**, horizontal **16:9**, máximo **900 KB**, formatos **PNG, JPG e WebP**, com preferência por WebP para arquivos mais leves. As medidas e a proporção são recomendações; outras dimensões/proporções válidas continuam aceitas sem corte ou distorção.

## Usar, substituir ou remover

- Selecione a imagem e confira a prévia no editor. Preencha **Descrição da imagem para acessibilidade**, utilizada no atributo `alt` da imagem do artigo e do cartão da listagem.
- **Pré-visualizar** mostra a capa completa acima do título, seguida do mesmo artigo com textos, ícones e índice. Funciona com imagem ainda não salva e com capa já cadastrada, incluindo rascunhos; não publica nem altera o banco.
- Para substituir, selecione outro arquivo e salve. Selecionar um novo arquivo desmarca a remoção anteriormente selecionada.
- Para remover, marque **Remover capa ao salvar**. O editor e a prévia deixam de exibir a imagem, mas a alteração só é persistida ao salvar o artigo. Desmarcar antes de salvar conserva a capa atual.
- Após salvar, a imagem aparece no início do artigo, acima do título, e no cartão da listagem do blog. Sem capa, não é gerada imagem padrão, contêiner reservado ou espaço de capa.

Capas existentes são reaproveitadas automaticamente nos artigos com ícones. Trocar o modelo ou salvar sem enviar outro arquivo não apaga a imagem. A remoção é uma ação explícita; enviar e remover simultaneamente é rejeitado com orientação clara, sem alterar a capa anterior.

## Persistência, validação e cache

Os arquivos e descrições continuam nos campos existentes `blog_posts.cover_image` e `cover_alt`, no banco persistente SQLite/PostgreSQL. Não há migração estrutural, dependência, credencial ou variável nova.

A prévia e o salvamento usam a mesma regra no servidor. O navegador verifica tamanho, assinatura do arquivo e decodificação; o servidor verifica novamente o conteúdo real com Pillow e o limite de 900 KB. Nome/extensão/MIME fornecidos pelo navegador não bastam. Em falha, a capa e os demais campos persistidos do artigo são preservados e o editor exibe uma mensagem clara, mantendo o texto preenchido e a prévia da capa anterior.

O artigo usa as proporções naturais da imagem. A listagem reserva um quadro horizontal e aplica `object-fit: contain`, exibindo a imagem completa, inclusive quando enviada em outra proporção. As URLs recebem a versão de atualização do artigo e a resposta da imagem contém ETag, para evitar que uma substituição continue mostrando a capa antiga.

Conteúdo, ícones, cores, fontes, índice, slug, título/descrição de SEO, data original de publicação e permissões de edição/publicação continuam com o funcionamento existente. Nenhum contrato, assinatura, cadastro ou configuração de envio é alterado.

## Verificação

Resultado: **243 testes aprovados com SQLite e 11 com PostgreSQL isolado, totalizando 254**, além dos testes de navegador nas sete larguras.

- Testes de PNG/JPG/WebP, 900 KB exatos, arquivo corrompido, formato falso, excesso de tamanho, conflito entre envio e remoção, preservação da linha completa em falhas, reutilização de capa antiga, descrição acessível, substituição, remoção e persistência após recriar a aplicação.
- Prévia da imagem enviada ou existente com os mesmos bytes da capa publicada; texto/ícones do artigo inalterados.
- PostgreSQL isolado com capa opcional, prévia, remoção e persistência após reinicialização.
- Chromium nas larguras **320, 360, 390, 430, 768, 1024 e 1440 px**: capa acima do título e no cartão, formatos PNG/JPG/WebP, imagem horizontal/vertical completa, prévia, arquivo inválido conservando a anterior, substituição, remoção, desfazer remoção antes de salvar e retorno ao artigo sem capa. Sem rolagem horizontal e sem alteração de texto ou SEO.

Relatório e capturas temporários: `/tmp/adelinha-blog-cover-results`. Os testes utilizam banco e imagens fictícios; não acessam nem reiniciam a Oracle. Após merge, aplique a atualização e reinicie o serviço pelo procedimento já usado na instalação, preservando o banco e o ambiente existentes.
