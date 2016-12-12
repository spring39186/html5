<?php

	include('AntiXSS.php');
	
    
    /*
     * @function   : san_out
     * @return     : String
     * @parameters : str: Content you want to change the character encoding
     * @newEncoding: Character encoding you want set
     * @description: Convert the character encoding of the string
     *               to newEncoding from currentEncoding. currentEncoding
     *               detecting by function so you only need give str and
     *               newEncoding to the setEncoding function.
     */	
	function san_out(){
		$username = $_POST["username"];
		$result = db_query("SELECT id,password, salt FROM users WHERE username = ’$username’");
		return $result = mysql_query($query);
	}
	
	
	    /*
     * @function   : san_wdata
     * @return     : String
     * @parameters : str: Content you want to filter and to sanitize
     * @description: Filter the content by method and encoding the result 
     */	
	function san_wdata($str){		
		$kwhd = $_GET["kwhd"];
		echo $kwhd;
	}

	    /*
     * @function   : san_rdata
     * @return     : String
     * @parameters : str: Content you want to filter
     * @description: Filter the content by method and encoding the result 
     */		
	function san_rdata($str){
		$str1 = trim($str);
		$str1 = AntiXSS::whitelistFilter($str1, '');
		if ($str1 == AntiXSS::$err)
			echo "<p>".$str1."</p>";

		return htmlentities($str, 1, "UTF-8", FALSE);
	}
?>