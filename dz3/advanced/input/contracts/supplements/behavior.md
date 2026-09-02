posts: ресурс постов: id, userId, title, body
comments: ресурс комментариев: id, postId, name, email, body
albums: ресурс альбомов: id, userId, title
photos: ресурс фото: id, albumId, title, url, thumbnailUrl
todos: ресурс дел: id, userId, title, completed
users: ресурс пользователей: id, name, username, email, address, phone, website, company
mutation: JSONPlaceholder ИМИТИРУЕТ создание/обновление/удаление — данные на сервере не сохраняются
create: POST /posts возвращает отправленный объект с id=101 (не перезапрашивать после)
update: PUT/PATCH возвращает отправленное тело обратно с сохранённым id (не перезапрашивать)
delete: DELETE возвращает пустой объект {} (ничего реально не удаляется)
verification: проверяй возвращённое тело напрямую (совпадение с payload и схемой), а не перезапрос ресурса
